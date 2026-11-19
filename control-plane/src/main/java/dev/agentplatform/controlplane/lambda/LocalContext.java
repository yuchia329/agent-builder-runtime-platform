package dev.agentplatform.controlplane.lambda;

import com.amazonaws.services.lambda.runtime.ClientContext;
import com.amazonaws.services.lambda.runtime.CognitoIdentity;
import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.LambdaLogger;
import java.nio.charset.StandardCharsets;

/** A Lambda {@link Context} for requests made inside the function (priming) and in tests. */
public final class LocalContext implements Context {

    private static final LambdaLogger LOGGER = new LambdaLogger() {
        @Override
        public void log(String message) {
            System.out.println(message);
        }

        @Override
        public void log(byte[] message) {
            log(new String(message, StandardCharsets.UTF_8));
        }
    };

    @Override
    public String getAwsRequestId() {
        return "local";
    }

    @Override
    public String getLogGroupName() {
        return "local";
    }

    @Override
    public String getLogStreamName() {
        return "local";
    }

    @Override
    public String getFunctionName() {
        return "control-plane";
    }

    @Override
    public String getFunctionVersion() {
        return "$LATEST";
    }

    @Override
    public String getInvokedFunctionArn() {
        return "arn:aws:lambda:local:000000000000:function:control-plane";
    }

    @Override
    public CognitoIdentity getIdentity() {
        return null;
    }

    @Override
    public ClientContext getClientContext() {
        return null;
    }

    @Override
    public int getRemainingTimeInMillis() {
        return 30_000;
    }

    @Override
    public int getMemoryLimitInMB() {
        return 1024;
    }

    @Override
    public LambdaLogger getLogger() {
        return LOGGER;
    }
}
