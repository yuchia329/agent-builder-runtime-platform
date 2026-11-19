package dev.agentplatform.controlplane.lambda;

import com.amazonaws.serverless.proxy.internal.LambdaContainerHandler;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** API Gateway HTTP API (payload 2.0) events, as the gateway sends them, for priming and tests. */
public final class HttpEvents {

    private HttpEvents() {}

    public static byte[] request(String method, String path, Map<String, String> headers, String body) {
        var json = LambdaContainerHandler.getObjectMapper();
        var event = json.createObjectNode();
        event.put("version", "2.0");
        event.put("routeKey", "$default");
        event.put("rawPath", path);
        event.put("rawQueryString", "");
        var headerNode = event.putObject("headers");
        headers.forEach((name, value) -> headerNode.put(name.toLowerCase(java.util.Locale.ROOT), value));
        var context = event.putObject("requestContext");
        context.put("requestId", "local");
        context.put("routeKey", "$default");
        context.put("stage", "$default");
        context.put("timeEpoch", System.currentTimeMillis());
        var http = context.putObject("http");
        http.put("method", method);
        http.put("path", path);
        http.put("protocol", "HTTP/1.1");
        http.put("sourceIp", "127.0.0.1");
        http.put("userAgent", "control-plane-local");
        if (body != null) {
            event.put("body", body);
        }
        event.put("isBase64Encoded", false);
        return json.writeValueAsString(event).getBytes(StandardCharsets.UTF_8);
    }
}
