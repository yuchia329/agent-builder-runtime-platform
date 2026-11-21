package dev.agentplatform.controlplane.tenant;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.secret.SecretUnavailableException;
import dev.agentplatform.controlplane.store.TenantStore;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Resolves the tenant from the {@code X-Api-Key} header on public routes (ADR 0026). First the
 * configured tenant's key ({@code TENANT_API_KEY} from the {@link SecretSource}, constant-time
 * compare), then, for a key shaped like {@link ApiKeys}' keys, the {@link TenantStore} by the key's
 * SHA-256. Any other key gets 401 without a store lookup. 503 if a lookup that was needed failed.
 */
@Component
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Api-Key";
    public static final String TENANT_ATTRIBUTE = "platform.tenantId";

    private final String tenantId;
    private final SecretSource secrets;
    private final TenantStore tenants;

    public ApiKeyAuthFilter(PlatformProperties props, SecretSource secrets, TenantStore tenants) {
        this.tenantId = props.tenant().id();
        this.secrets = secrets;
        this.tenants = tenants;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        var path = request.getRequestURI();
        return !(path.startsWith("/agents") || path.startsWith("/runs"));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        var presented = request.getHeader(HEADER);
        if (presented == null || presented.isEmpty()) {
            unauthorized(response);
            return;
        }
        boolean lookupFailed = false;
        try {
            var configured = secrets.get(Secret.TENANT_API_KEY);
            if (configured != null && MessageDigest.isEqual(configured.getBytes(StandardCharsets.UTF_8),
                    presented.getBytes(StandardCharsets.UTF_8))) {
                pass(request, response, chain, tenantId);
                return;
            }
        } catch (SecretUnavailableException e) {
            logger.warn("API key check: secret unavailable: " + e.getMessage());
            lookupFailed = true;
        }
        if (ApiKeys.isWellFormed(presented)) {
            Optional<String> stored;
            try {
                stored = tenants.tenantForKey(ApiKeys.hash(presented));
            } catch (RuntimeException e) {
                // Never the key or its hash: only the error type.
                logger.warn("API key check: tenant store unavailable (" + e.getClass().getSimpleName() + ")");
                problem(response, HttpStatus.SERVICE_UNAVAILABLE, "Service Unavailable", "Try again shortly");
                return;
            }
            if (stored.isPresent()) {
                pass(request, response, chain, stored.get());
                return;
            }
        }
        if (lookupFailed) {
            problem(response, HttpStatus.SERVICE_UNAVAILABLE, "Service Unavailable", "Try again shortly");
            return;
        }
        unauthorized(response);
    }

    private static void pass(HttpServletRequest request, HttpServletResponse response, FilterChain chain,
                             String tenant) throws ServletException, IOException {
        request.setAttribute(TENANT_ATTRIBUTE, tenant);
        chain.doFilter(request, response);
    }

    private static void unauthorized(HttpServletResponse response) throws IOException {
        problem(response, HttpStatus.UNAUTHORIZED, "Unauthorized", "Missing or invalid X-Api-Key");
    }

    private static void problem(HttpServletResponse response, HttpStatus status, String title, String detail)
            throws IOException {
        response.setStatus(status.value());
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.getWriter().write("{\"title\":\"" + title + "\",\"status\":" + status.value()
                + ",\"detail\":\"" + detail + "\"}");
    }
}
