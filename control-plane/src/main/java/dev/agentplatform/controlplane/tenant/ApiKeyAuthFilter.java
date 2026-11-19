package dev.agentplatform.controlplane.tenant;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.secret.SecretUnavailableException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Resolves the tenant from the {@code X-Api-Key} header on public routes. M0 has one tenant whose
 * key is {@code TENANT_API_KEY} from the {@link SecretSource} (SSM on AWS, read on first use);
 * later the key is looked up in the store by its hash. 503 if the secret store cannot be read.
 */
@Component
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Api-Key";
    public static final String TENANT_ATTRIBUTE = "platform.tenantId";

    private final String tenantId;
    private final SecretSource secrets;

    public ApiKeyAuthFilter(PlatformProperties props, SecretSource secrets) {
        this.tenantId = props.tenant().id();
        this.secrets = secrets;
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
        String apiKey;
        try {
            apiKey = presented == null ? null : secrets.get(Secret.TENANT_API_KEY);
        } catch (SecretUnavailableException e) {
            logger.warn("API key check failed: " + e.getMessage());
            problem(response, HttpStatus.SERVICE_UNAVAILABLE, "Service Unavailable", "Try again shortly");
            return;
        }
        if (apiKey == null || !MessageDigest.isEqual(apiKey.getBytes(StandardCharsets.UTF_8),
                presented.getBytes(StandardCharsets.UTF_8))) {
            problem(response, HttpStatus.UNAUTHORIZED, "Unauthorized", "Missing or invalid X-Api-Key");
            return;
        }
        request.setAttribute(TENANT_ATTRIBUTE, tenantId);
        chain.doFilter(request, response);
    }

    private static void problem(HttpServletResponse response, HttpStatus status, String title, String detail)
            throws IOException {
        response.setStatus(status.value());
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.getWriter().write("{\"title\":\"" + title + "\",\"status\":" + status.value()
                + ",\"detail\":\"" + detail + "\"}");
    }
}
