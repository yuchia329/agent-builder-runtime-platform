package dev.agentplatform.controlplane.tenant;

import dev.agentplatform.controlplane.config.PlatformProperties;
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
 * key comes from config; later the key is looked up in the store by its hash.
 */
@Component
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Api-Key";
    public static final String TENANT_ATTRIBUTE = "platform.tenantId";

    private final String tenantId;
    private final byte[] apiKey;

    public ApiKeyAuthFilter(PlatformProperties props) {
        this.tenantId = props.tenant().id();
        this.apiKey = props.tenant().apiKey().getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/agents");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        var presented = request.getHeader(HEADER);
        if (presented == null
                || !MessageDigest.isEqual(apiKey, presented.getBytes(StandardCharsets.UTF_8))) {
            response.setStatus(HttpStatus.UNAUTHORIZED.value());
            response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
            response.getWriter().write(
                    "{\"title\":\"Unauthorized\",\"status\":401,\"detail\":\"Missing or invalid X-Api-Key\"}");
            return;
        }
        request.setAttribute(TENANT_ATTRIBUTE, tenantId);
        chain.doFilter(request, response);
    }
}
