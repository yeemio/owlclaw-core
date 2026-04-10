package dev.owlclaw.examples;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public class OwlClawApiClient {
    private final HttpClient client;
    private final String baseUrl;
    private final int maxRetries;
    private final Duration requestTimeout;

    public OwlClawApiClient(String baseUrl) {
        this(baseUrl, 2, Duration.ofSeconds(10));
    }

    public OwlClawApiClient(String baseUrl, int maxRetries, Duration requestTimeout) {
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        this.baseUrl = baseUrl;
        this.maxRetries = maxRetries;
        this.requestTimeout = requestTimeout;
    }

    public String triggerAgent(String agentId, String message) throws IOException, InterruptedException {
        return triggerAgent(agentId, message, "idem-" + agentId);
    }

    public String triggerAgent(String agentId, String message, String idempotencyKey)
            throws IOException, InterruptedException {
        String body = "{\"agent_id\":\"" + agentId + "\",\"message\":\"" + message + "\"}";
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/agent/trigger"))
                .timeout(requestTimeout)
                .header("Content-Type", "application/json")
                .header("Idempotency-Key", idempotencyKey)
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> response = sendWithRetry(request);
        return handleResponse(response);
    }

    public String queryStatus(String runId) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/agent/status/" + runId))
                .timeout(requestTimeout)
                .GET()
                .build();
        HttpResponse<String> response = sendWithRetry(request);
        return handleResponse(response);
    }

    private HttpResponse<String> sendWithRetry(HttpRequest request) throws IOException, InterruptedException {
        IOException last = null;
        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                return client.send(request, HttpResponse.BodyHandlers.ofString());
            } catch (IOException exc) {
                last = exc;
                if (attempt == maxRetries) {
                    throw exc;
                }
            }
        }
        throw last == null ? new IOException("unknown request failure") : last;
    }

    private String handleResponse(HttpResponse<String> response) throws IOException {
        if (response.statusCode() >= 400) {
            throw new IOException(
                    "request failed: status=" + response.statusCode() + " body=" + response.body()
            );
        }
        return response.body();
    }
}
