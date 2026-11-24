FROM maven:3.9.11-eclipse-temurin-25 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B package -DskipTests

FROM eclipse-temurin:25-jre
WORKDIR /app
RUN groupadd -r eris && useradd -r -g eris eris
COPY --from=build /build/target/eristutor-backend-1.0.0-SNAPSHOT.jar /app/app.jar
USER eris
EXPOSE 8080
ENV SPRING_PROFILES_ACTIVE=prod
ENTRYPOINT ["java", "-XX:+UseZGC", "-jar", "/app/app.jar"]
