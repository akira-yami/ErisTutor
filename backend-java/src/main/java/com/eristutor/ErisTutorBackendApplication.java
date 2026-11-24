package com.eristutor;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class ErisTutorBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(ErisTutorBackendApplication.class, args);
    }
}
