# Cross-language Java Example

This directory provides a Java baseline project for protocol-level integration checks.

## Prerequisites

1. JDK 17
2. Maven 3.9+

## Structure

1. `pom.xml`: Java 17 baseline project configuration.
2. `src/main/java/io/owlclaw/examples/crosslang/Main.java`: entry point.
3. `src/main/java/io/owlclaw/examples/crosslang/GatewayClient.java`: simple HTTP helper.
4. `src/main/java/dev/owlclaw/examples/OwlClawApiClient.java`: retry/idempotency baseline client.

## Quick Check

```bash
mvn -q -DskipTests package
```

## Scenario Commands

```bash
export OWLCLAW_GATEWAY_BASE_URL=http://localhost:8000
export OWLCLAW_API_TOKEN=<token>

java -cp target/classes io.owlclaw.examples.crosslang.Main trigger
java -cp target/classes io.owlclaw.examples.crosslang.Main query <run_id>
java -cp target/classes io.owlclaw.examples.crosslang.Main error
```
