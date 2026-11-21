package dev.agentplatform.controlplane.tenant;

import dev.agentplatform.controlplane.store.DynamoDbTenantStore;
import dev.agentplatform.controlplane.store.TenantStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.io.PrintStream;
import java.math.BigDecimal;
import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * Command line for tenants, straight on the control-plane table with the same store code the app
 * uses (no admin HTTP route). Run through Gradle:
 *
 * <pre>
 * ./gradlew tenantAdmin --args="create tenant-b --max-running 50 --daily-usd 5 --table agent-platform-dev-control-plane"
 * ./gradlew tenantAdmin --args="add-key tenant-b --table ..."
 * ./gradlew tenantAdmin --args="set-limits tenant-b --max-running 20 --daily-usd 2 --table ..."
 * ./gradlew tenantAdmin --args="show tenant-b --table ..."
 * </pre>
 *
 * {@code create} and {@code add-key} print the new key once: it is not stored and cannot be shown
 * again. AWS credentials and region come from the environment ({@code AWS_PROFILE}, default
 * {@code default}; the {@code prod} profile is refused); {@code AWS_ENDPOINT_URL_DYNAMODB} points
 * it at DynamoDB Local.
 */
public final class TenantAdmin {

    private final TenantStore store;
    private final TenantLimits defaults;
    private final Clock clock;
    private final PrintStream out;

    TenantAdmin(TenantStore store, TenantLimits defaults, Clock clock, PrintStream out) {
        this.store = store;
        this.defaults = defaults;
        this.clock = clock;
        this.out = out;
    }

    public static void main(String[] args) {
        if ("prod".equals(System.getenv("AWS_PROFILE"))) {
            System.err.println("This project never uses the prod profile.");
            System.exit(2);
        }
        var parsed = Args.parse(args);
        if (parsed.table() == null) {
            System.err.println("--table <control-plane table name> is required");
            System.exit(2);
        }
        try (var dynamo = DynamoDbClient.create()) {
            var admin = new TenantAdmin(new DynamoDbTenantStore(dynamo, parsed.table()),
                    new TenantLimits(50, new BigDecimal("5")), Clock.systemUTC(), System.out);
            System.exit(admin.run(parsed));
        }
    }

    /** Returns the exit code. */
    int run(Args args) {
        try {
            switch (args.command()) {
                case "create" -> {
                    var limits = args.limits(defaults);
                    store.create(new Tenant(args.tenantId(), limits, clock.instant()));
                    out.println("Created tenant " + args.tenantId() + " (max running " + limits.maxRunning()
                            + ", daily USD " + limits.dailyUsd().toPlainString() + ")");
                    addKey(args.tenantId());
                }
                case "add-key" -> addKey(args.tenantId());
                case "set-limits" -> {
                    var current = store.find(args.tenantId())
                            .orElseThrow(() -> new TenantStore.TenantNotFoundException(args.tenantId()));
                    var limits = args.limits(current.limits());
                    store.setLimits(args.tenantId(), limits);
                    out.println("Limits of " + args.tenantId() + ": max running " + limits.maxRunning()
                            + ", daily USD " + limits.dailyUsd().toPlainString());
                }
                case "show" -> {
                    var tenant = store.find(args.tenantId())
                            .orElseThrow(() -> new TenantStore.TenantNotFoundException(args.tenantId()));
                    out.println(tenant.id() + ": max running " + tenant.limits().maxRunning() + ", daily USD "
                            + tenant.limits().dailyUsd().toPlainString() + ", created " + tenant.createdAt());
                }
                default -> throw new IllegalArgumentException("unknown command: " + args.command()
                        + " (create, add-key, set-limits, show)");
            }
            return 0;
        } catch (IllegalArgumentException | TenantStore.TenantAlreadyExistsException
                 | TenantStore.TenantNotFoundException e) {
            out.println("Error: " + e.getMessage());
            return 1;
        }
    }

    private void addKey(String tenantId) {
        var key = ApiKeys.generate();
        store.addApiKey(tenantId, key.hash(), key.prefix(), clock.instant());
        out.println("API key for " + tenantId + " (shown once, store it now; only its hash is kept):");
        out.println(key.key());
    }

    /** {@code <command> <tenant id> [--max-running N] [--daily-usd X] [--table NAME]}. */
    record Args(String command, String tenantId, Integer maxRunning, BigDecimal dailyUsd, String table) {

        static Args parse(String[] argv) {
            var positional = new ArrayList<String>();
            Integer maxRunning = null;
            BigDecimal dailyUsd = null;
            String table = null;
            var list = List.of(argv);
            for (int i = 0; i < list.size(); i++) {
                var arg = list.get(i);
                switch (arg) {
                    case "--max-running" -> maxRunning = Integer.valueOf(value(list, ++i, arg));
                    case "--daily-usd" -> dailyUsd = new BigDecimal(value(list, ++i, arg));
                    case "--table" -> table = value(list, ++i, arg);
                    default -> positional.add(arg);
                }
            }
            if (positional.size() != 2) {
                throw new IllegalArgumentException(
                        "usage: <create|add-key|set-limits|show> <tenant id> [--max-running N] [--daily-usd X] --table T");
            }
            Tenant.requireValidId(positional.get(1));
            return new Args(positional.get(0), positional.get(1), maxRunning, dailyUsd, table);
        }

        TenantLimits limits(TenantLimits base) {
            return new TenantLimits(maxRunning == null ? base.maxRunning() : maxRunning,
                    dailyUsd == null ? base.dailyUsd() : dailyUsd);
        }

        private static String value(List<String> list, int i, String flag) {
            if (i >= list.size()) {
                throw new IllegalArgumentException(flag + " needs a value");
            }
            return list.get(i);
        }
    }
}
