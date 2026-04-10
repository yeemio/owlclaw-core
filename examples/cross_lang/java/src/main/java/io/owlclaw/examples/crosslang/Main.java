package io.owlclaw.examples.crosslang;

import java.net.http.HttpResponse;

public final class Main {
    private Main() {
    }

    public static void main(String[] args) throws Exception {
        String baseUrl = System.getenv().getOrDefault("OWLCLAW_GATEWAY_BASE_URL", "http://localhost:8000");
        String token = System.getenv().getOrDefault("OWLCLAW_API_TOKEN", "");
        GatewayClient client = new GatewayClient(baseUrl, 5, 2);

        if (args.length == 0) {
            System.out.println("Usage: java Main <trigger|query|error|health> [runId]");
            return;
        }

        switch (args[0]) {
            case "trigger" -> {
                HttpResponse<String> response = client.triggerAgent(token, "cross_lang_smoke_java");
                print("trigger", response);
            }
            case "query" -> {
                if (args.length < 2) {
                    throw new IllegalArgumentException("query requires runId argument");
                }
                HttpResponse<String> response = client.queryRunStatus(token, args[1]);
                print("query", response);
            }
            case "error" -> {
                HttpResponse<String> response = client.sendInvalidTriggerPayload();
                print("error", response);
            }
            case "health" -> System.out.println("health status: " + client.healthStatusCode());
            default -> System.out.println("Unknown scenario: " + args[0]);
        }
    }

    private static void print(String scenario, HttpResponse<String> response) {
        System.out.println("scenario=" + scenario);
        System.out.println("status=" + response.statusCode());
        System.out.println("body=" + response.body());
    }
}
