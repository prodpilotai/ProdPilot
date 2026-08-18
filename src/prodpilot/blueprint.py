"""Production blueprint definitions for the supported stacks.

Scope is Phase 1 module 1.2. A blueprint declares every file, config, and code
pattern a deployment-ready project must have, per Layer 0 in Section 3 of the
Complete Solution Document.

This module is data only. It declares what must be present. It does not check
for it and it does not fix it. Rule checks are Phase 2. Fix generation is
Phase 3.

Domains and priorities follow the rule system table in Section 4, so the Phase 2
audit engine can map blueprint items onto rule definitions without renaming
anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stack(str, Enum):
    """Stacks ProdPilot supports in v1.

    Section 9 of the Complete Solution Document limits v1 to Node.js 20 LTS with
    Express and React 18 with Vite 5. Django is out of scope.
    """

    NODE_EXPRESS = "node_express"
    REACT_VITE = "react_vite"
    UNRECOGNIZED = "unrecognized"


class Domain(str, Enum):
    """Rule domains from Section 4."""

    SECURITY = "security"
    SECRETS = "secrets"
    ENVIRONMENT = "environment"
    BUILD = "build"
    CONNECTIVITY = "connectivity"
    API = "api"
    STRUCTURE = "structure"
    OBSERVABILITY = "observability"
    GIT_HYGIENE = "git_hygiene"


class Priority(str, Enum):
    """Priority tiers from Section 4, P0 critical through P5 low."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


@dataclass(frozen=True)
class BlueprintItem:
    """One requirement a deployment-ready project must satisfy.

    item_id is stable and is what a Phase 2 rule will reference. Nothing here
    describes how to verify the requirement or how to fix it.
    """

    item_id: str
    requirement: str
    domain: Domain
    priority: Priority

    def to_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "requirement": self.requirement,
            "domain": self.domain.value,
            "priority": self.priority.value,
        }


@dataclass(frozen=True)
class ProductionBlueprint:
    """The complete deployment-ready definition for one stack."""

    stack: Stack
    display_name: str
    runtime: str
    required_files: tuple[BlueprintItem, ...] = field(default_factory=tuple)
    required_configs: tuple[BlueprintItem, ...] = field(default_factory=tuple)
    required_code_patterns: tuple[BlueprintItem, ...] = field(default_factory=tuple)

    @property
    def item_count(self) -> int:
        return (
            len(self.required_files)
            + len(self.required_configs)
            + len(self.required_code_patterns)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "stack": self.stack.value,
            "display_name": self.display_name,
            "runtime": self.runtime,
            "item_count": self.item_count,
            "required_files": [i.to_dict() for i in self.required_files],
            "required_configs": [i.to_dict() for i in self.required_configs],
            "required_code_patterns": [i.to_dict() for i in self.required_code_patterns],
        }


NODE_EXPRESS_BLUEPRINT = ProductionBlueprint(
    stack=Stack.NODE_EXPRESS,
    display_name="Node.js with Express",
    runtime="Node.js 20 LTS",
    required_files=(
        BlueprintItem("node.file.dockerfile", "Dockerfile present at project root", Domain.BUILD, Priority.P1),
        BlueprintItem("node.file.dockerignore", ".dockerignore present at project root", Domain.BUILD, Priority.P1),
        BlueprintItem("node.file.env_example", ".env.example present and covering every environment key the code reads", Domain.ENVIRONMENT, Priority.P0),
        BlueprintItem("node.file.gitignore", ".gitignore present at project root", Domain.GIT_HYGIENE, Priority.P5),
        BlueprintItem("node.file.ci_workflow", "GitHub Actions workflow present under .github/workflows", Domain.BUILD, Priority.P1),
    ),
    required_configs=(
        BlueprintItem("node.config.gitignore_node_modules", ".gitignore excludes node_modules", Domain.GIT_HYGIENE, Priority.P5),
        BlueprintItem("node.config.gitignore_env", ".gitignore excludes .env and .env.production", Domain.SECRETS, Priority.P0),
        BlueprintItem("node.config.start_script", "package.json declares a start script that runs the server without a dev watcher", Domain.BUILD, Priority.P1),
        BlueprintItem("node.config.engine_pin", "package.json pins the Node engine to 20 LTS", Domain.BUILD, Priority.P1),
        BlueprintItem("node.config.multistage_build", "Dockerfile uses a multi-stage build so build tooling stays out of the runtime image", Domain.BUILD, Priority.P1),
        BlueprintItem("node.config.non_root_user", "Dockerfile runs the process as a non-root user", Domain.SECURITY, Priority.P0),
    ),
    required_code_patterns=(
        BlueprintItem("node.code.helmet_registered", "helmet is registered before any route definition", Domain.SECURITY, Priority.P0),
        BlueprintItem("node.code.cors_from_env", "CORS origin is read from an environment variable rather than a wildcard", Domain.SECURITY, Priority.P0),
        BlueprintItem("node.code.csp_headers", "Content Security Policy and HTTPS redirect headers are set", Domain.SECURITY, Priority.P0),
        BlueprintItem("node.code.no_hardcoded_secrets", "no API key, token, or password literal appears in source", Domain.SECRETS, Priority.P0),
        BlueprintItem("node.code.port_from_env", "the listening port is read from the environment", Domain.ENVIRONMENT, Priority.P0),
        BlueprintItem("node.code.db_credentials_from_env", "database credentials are read from the environment", Domain.CONNECTIVITY, Priority.P1),
        BlueprintItem("node.code.connection_pooling", "database access uses a connection pool rather than a per-request connection", Domain.CONNECTIVITY, Priority.P1),
        BlueprintItem("node.code.rate_limiting", "public routes are rate limited", Domain.API, Priority.P2),
        BlueprintItem("node.code.api_versioning", "routes are mounted under a versioned prefix", Domain.API, Priority.P2),
        BlueprintItem("node.code.error_middleware", "a centralised error handler returns a consistent error shape and leaks no stack traces", Domain.API, Priority.P2),
        BlueprintItem("node.code.service_layer", "database calls live in a service layer rather than inside controllers", Domain.STRUCTURE, Priority.P3),
        BlueprintItem("node.code.health_endpoint", "a health check endpoint is exposed", Domain.OBSERVABILITY, Priority.P4),
        BlueprintItem("node.code.structured_logging", "structured logging is configured rather than bare console output", Domain.OBSERVABILITY, Priority.P4),
        BlueprintItem("node.code.graceful_shutdown", "SIGTERM is handled so the server drains connections before exit", Domain.OBSERVABILITY, Priority.P4),
    ),
)


REACT_VITE_BLUEPRINT = ProductionBlueprint(
    stack=Stack.REACT_VITE,
    display_name="React with Vite",
    runtime="React 18 with Vite 5, served as static assets",
    required_files=(
        BlueprintItem("react.file.dockerfile", "Dockerfile present at project root", Domain.BUILD, Priority.P1),
        BlueprintItem("react.file.dockerignore", ".dockerignore present at project root", Domain.BUILD, Priority.P1),
        BlueprintItem("react.file.nginx_conf", "nginx configuration present for serving the built single page application", Domain.BUILD, Priority.P1),
        BlueprintItem("react.file.env_example", ".env.example present and covering every VITE_ key the code reads", Domain.ENVIRONMENT, Priority.P0),
        BlueprintItem("react.file.gitignore", ".gitignore present at project root", Domain.GIT_HYGIENE, Priority.P5),
        BlueprintItem("react.file.ci_workflow", "GitHub Actions workflow present under .github/workflows", Domain.BUILD, Priority.P1),
    ),
    required_configs=(
        BlueprintItem("react.config.gitignore_node_modules", ".gitignore excludes node_modules", Domain.GIT_HYGIENE, Priority.P5),
        BlueprintItem("react.config.gitignore_dist", ".gitignore excludes the dist build output", Domain.GIT_HYGIENE, Priority.P5),
        BlueprintItem("react.config.gitignore_env", ".gitignore excludes .env and .env.production", Domain.SECRETS, Priority.P0),
        BlueprintItem("react.config.build_script", "package.json declares a build script that produces the production bundle", Domain.BUILD, Priority.P1),
        BlueprintItem("react.config.multistage_build", "Dockerfile builds in one stage and serves the built assets from a static image", Domain.BUILD, Priority.P1),
        BlueprintItem("react.config.spa_fallback", "nginx falls back to index.html so client side routes resolve on refresh", Domain.BUILD, Priority.P1),
        BlueprintItem("react.config.non_root_user", "Dockerfile runs the server process as a non-root user", Domain.SECURITY, Priority.P0),
    ),
    required_code_patterns=(
        BlueprintItem("react.code.api_url_from_env", "the API base URL is read from import.meta.env rather than hardcoded", Domain.ENVIRONMENT, Priority.P0),
        BlueprintItem("react.code.no_hardcoded_backend_urls", "no backend host literal appears in source", Domain.ENVIRONMENT, Priority.P0),
        BlueprintItem("react.code.no_hardcoded_secrets", "no API key or token literal appears in source", Domain.SECRETS, Priority.P0),
        BlueprintItem("react.code.security_headers", "the serving layer sets Content Security Policy and related security headers", Domain.SECURITY, Priority.P0),
        BlueprintItem("react.code.error_boundary", "an error boundary wraps the root of the component tree", Domain.STRUCTURE, Priority.P3),
        BlueprintItem("react.code.catch_all_route", "the client router handles unmatched routes with a catch all", Domain.STRUCTURE, Priority.P3),
    ),
)


_BLUEPRINTS: dict[Stack, ProductionBlueprint] = {
    Stack.NODE_EXPRESS: NODE_EXPRESS_BLUEPRINT,
    Stack.REACT_VITE: REACT_VITE_BLUEPRINT,
}


def get_blueprint(stack: Stack) -> ProductionBlueprint | None:
    """Return the production blueprint for a stack.

    Returns None for Stack.UNRECOGNIZED, which has no blueprint by definition.
    Callers must handle None rather than assuming a blueprint exists.
    """
    return _BLUEPRINTS.get(stack)


def supported_stacks() -> tuple[Stack, ...]:
    """Return the stacks that have a blueprint."""
    return tuple(_BLUEPRINTS)
