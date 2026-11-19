package dev.agentplatform.controlplane.secret;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import software.amazon.awssdk.services.ssm.SsmClient;
import software.amazon.awssdk.services.ssm.model.GetParametersRequest;
import software.amazon.awssdk.services.ssm.model.GetParametersResponse;
import software.amazon.awssdk.services.ssm.model.Parameter;
import software.amazon.awssdk.services.ssm.model.ParameterType;
import software.amazon.awssdk.services.ssm.model.SsmException;

/**
 * SSM Parameter Store in memory: only {@code GetParameters}, with SSM's limit of 10 names and
 * decryption required for SecureString values. Counts calls; {@link #failing} makes the next calls
 * throw like an unreachable service.
 */
public class FakeSsm implements SsmClient {

    public final Map<String, String> parameters = new ConcurrentHashMap<>();
    public final AtomicInteger calls = new AtomicInteger();
    public final List<GetParametersRequest> requests = new ArrayList<>();
    public volatile boolean failing;

    public FakeSsm put(String name, String value) {
        parameters.put(name, value);
        return this;
    }

    @Override
    public synchronized GetParametersResponse getParameters(GetParametersRequest request) {
        calls.incrementAndGet();
        requests.add(request);
        if (failing) {
            throw SsmException.builder().message("Rate exceeded").statusCode(400).build();
        }
        if (request.names().size() > 10) {
            throw SsmException.builder().message("Member must have length less than or equal to 10").build();
        }
        if (!Boolean.TRUE.equals(request.withDecryption())) {
            throw new AssertionError("SecureString parameters must be read with decryption");
        }
        var found = new ArrayList<Parameter>();
        var invalid = new ArrayList<String>();
        for (var name : request.names()) {
            var value = parameters.get(name);
            if (value == null) {
                invalid.add(name);
            } else {
                found.add(Parameter.builder().name(name).value(value).type(ParameterType.SECURE_STRING).version(2L).build());
            }
        }
        return GetParametersResponse.builder().parameters(found).invalidParameters(invalid).build();
    }

    @Override
    public String serviceName() {
        return "ssm";
    }

    @Override
    public void close() {}
}
