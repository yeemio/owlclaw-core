package io.owlclaw.examples.crosslang;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.UUID;

public final class GatewayClient {
    private final HttpClient client;
    private final URI baseUri;
    private final int retryAttempts;

    public GatewayClient(String baseUrl) {
        this(baseUrl, 5, 2);
    }

    public GatewayClient(String baseUrl, int timeoutSeconds, int retryAttempts) {
        this.client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(timeoutSeconds)).build();
        this.baseUri = URI.create(baseUrl);
        this.retryAttempts = Math.max(1, retryAttempts);
    }

    public int healthStatusCode() throws Exception {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(baseUri.resolve("/health"))
                .timeout(Duration.ofSeconds(5))
                .GET()
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        return response.statusCode();
    }

    public HttpResponse<String> triggerAgent(String token, String eventName) throws Exception {
        String idempotencyKey = UUID.randomUUID().toString();
        String payload = String.format(
                "{\"trigger_type\":\"manual\",\"event\":\"%s\",\"context\":{\"source\":\"java\"}}",
                eventName
        );
        HttpRequest request = HttpRequest.newBuilder()
                .uri(baseUri.resolve("/api/v1/triggers"))
                .timeout(Duration.ofSeconds(5))
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + token)
                .header("Idempotency-Key", idempotencyKey)
                .POST(HttpRequest.BodyPublishers.ofString(payload))
                .build();
        return sendWithRetry(request);
    }

    public HttpResponse<String> queryRunStatus(String token, String runId) throws Exception {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(baseUri.resolve("/api/v1/runs/" + runId))
                .timeout(Duration.ofSeconds(5))
                .header("Authorization", "Bearer " + token)
                .GET()
                .build();
        return sendWithRetry(request);
    }

    public HttpResponse<String> sendInvalidTriggerPayload() throws Exception {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(baseUri.resolve("/api/v1/triggers"))
                .timeout(Duration.ofSeconds(5))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"invalid\":true}"))
                .build();
        return sendWithRetry(request);
    }

    private HttpResponse<String> sendWithRetry(HttpRequest request) throws Exception {
        Exception lastException = null;
        for (int attempt = 1; attempt <= retryAttempts; attempt++) {
            try {
                HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() >= 500 && attempt < retryAttempts) {
                    continue;
                }
                return response;
            } catch (Exception ex) {
                lastException = ex;
                if (attempt >= retryAttempts) {
                    throw ex;
                }
            }
        }
        throw lastException != null ? lastException : new IllegalStateException("retry loop exited unexpectedly");
    }
}
