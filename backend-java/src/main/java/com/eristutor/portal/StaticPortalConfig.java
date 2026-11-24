package com.eristutor.portal;

import com.eristutor.common.ErisTutorProperties;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.nio.file.Files;
import java.nio.file.Path;

@Configuration
public class StaticPortalConfig implements WebMvcConfigurer {

    private final ErisTutorProperties properties;

    public StaticPortalConfig(ErisTutorProperties properties) {
        this.properties = properties;
    }

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        String dirCfg = properties.richchat() != null ? properties.richchat().dir() : null;
        if (dirCfg != null && !dirCfg.isBlank()) {
            Path resolved = Path.of(dirCfg).toAbsolutePath().normalize();
            if (Files.isDirectory(resolved)) {
                String uri = resolved.toUri().toString();
                if (!uri.endsWith("/")) uri += "/";
                registry.addResourceHandler("/**")
                        .addResourceLocations(uri, "classpath:/static/");
                return;
            }
        }
        registry.addResourceHandler("/**")
                .addResourceLocations("classpath:/static/");
    }

    @Override
    public void addViewControllers(ViewControllerRegistry registry) {
        registry.addViewController("/").setViewName("forward:/index.html");
    }
}
