package dev.agentplatform.controlplane.lambda;

import com.amazonaws.serverless.exceptions.ContainerInitializationException;
import com.amazonaws.serverless.proxy.internal.LambdaContainerHandler;
import com.amazonaws.serverless.proxy.model.AwsProxyResponse;
import com.amazonaws.serverless.proxy.model.HttpApiV2ProxyRequest;
import com.amazonaws.serverless.proxy.spring.SpringBootLambdaContainerHandler;
import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestStreamHandler;
import dev.agentplatform.controlplane.ControlPlaneApplication;
import dev.agentplatform.controlplane.run.Sweeps;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.trace.TraceExport;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import org.crac.Core;
import org.crac.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.context.support.WebApplicationContextUtils;

/**
 * The control plane's AWS Lambda entry (ADR 0017). Two kinds of event arrive:
 *
 * <ul>
 *   <li>API Gateway HTTP API events (payload 2.0): passed to the same Spring MVC app that runs
 *       locally, through aws-serverless-java-container.
 *   <li>{@code {"sweep": "all"}} from EventBridge Scheduler: runs {@link Sweeps#runOnce()} and
 *       answers {@code {"requeued": n, "posted": n, "expired": n}}. Only a direct invoke
 *       (IAM {@code lambda:InvokeFunction}) can send it; HTTP requests always carry {@code routeKey}.
 * </ul>
 *
 * <p>Spring starts in the static initializer, so with SnapStart it starts once, before the
 * snapshot. Then {@link #beforeCheckpoint} sends a few requests that need no network through the
 * app ({@link Priming}), so the classes a first real request needs are loaded and in the snapshot.
 * {@code LAMBDA_PRIMING=off} skips that (for measuring, ADR 0017); its spans are dropped, and the
 * span exporter closes before the snapshot (ADR 0022). {@link #afterRestore} starts
 * loading the secrets in the background (ADR 0018). The in-process sweep schedule is
 * always off here: a frozen function cannot keep time.
 */
public class LambdaHandler implements RequestStreamHandler, Resource {

    private static final Logger log = LoggerFactory.getLogger(LambdaHandler.class);

    private static final SpringBootLambdaContainerHandler<HttpApiV2ProxyRequest, AwsProxyResponse> HTTP;

    static {
        System.setProperty("platform.sweep.interval-seconds", "0");
        try {
            HTTP = SpringBootLambdaContainerHandler.getHttpApiV2ProxyHandler(ControlPlaneApplication.class);
        } catch (ContainerInitializationException e) {
            throw new IllegalStateException("Control plane failed to start", e);
        }
    }

    public LambdaHandler() {
        Core.getGlobalContext().register(this);
    }

    @Override
    public void handleRequest(InputStream input, OutputStream output, Context context) throws IOException {
        var event = input.readAllBytes();
        var tree = LambdaContainerHandler.getObjectMapper().readTree(event);
        if (tree.isObject() && tree.has("sweep") && !tree.has("routeKey")) {
            output.write(sweep(tree.path("sweep").asString("")));
            return;
        }
        HTTP.proxyStream(new ByteArrayInputStream(event), output, context);
    }

    private byte[] sweep(String which) {
        if (!"all".equals(which)) {
            throw new IllegalArgumentException("Unknown sweep \"" + which + "\"; only \"all\" exists");
        }
        var result = bean(Sweeps.class).runOnce();
        return ("{\"requeued\":" + result.requeued() + ",\"posted\":" + result.posted() + ",\"expired\":"
                + result.expired() + ",\"stalled\":" + result.stalled() + "}").getBytes(StandardCharsets.UTF_8);
    }

    <T> T bean(Class<T> type) {
        return WebApplicationContextUtils.getRequiredWebApplicationContext(HTTP.getServletContext()).getBean(type);
    }

    @Override
    public void beforeCheckpoint(org.crac.Context<? extends Resource> context) {
        if ("off".equalsIgnoreCase(System.getenv("LAMBDA_PRIMING"))) {
            return;
        }
        log.info("Priming before the SnapStart snapshot");
        var traces = bean(TraceExport.class);
        traces.pause(); // priming requests are not real traffic
        try {
            Priming.run(this);
        } finally {
            traces.resume();
        }
    }

    @Override
    public void afterRestore(org.crac.Context<? extends Resource> context) {
        // Nothing to reopen: AWS SDK clients connect lazily, and priming opened no connection.
        // Secrets (SSM, ADR 0018) start loading in the background, alongside the first request's
        // own work, instead of when that request first needs one.
        bean(SecretSource.class).prefetch();
    }
}
