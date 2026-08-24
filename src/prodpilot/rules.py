"""Rule schema and rule store for the audit engine.

Scope is Phase 2 module 2.1. This module defines what a rule is and holds the
frozen v1 rule definitions. It runs no checks and produces no fixes.

Section 4 of the Complete Solution Document states that every rule carries a
rule_id, a domain, a priority, a scope, a check_type, a fix_type, and either a
fix_template_id, an extraction_spec or a constraint_template depending on the
fix type. That is exactly the shape declared here.

The Domain and Priority vocabularies come from blueprint.py rather than being
redefined, so a rule and the blueprint item it enforces cannot drift apart.
Every rule names the blueprint item it comes from, which keeps the two sets in
a one to one relationship that a reviewer can verify by eye.

What is deliberately absent:

  Check execution.  AST parsing, file existence checking and entropy scanning
  are modules 2.2 and 2.3. A rule declares which of the three applies to it and
  nothing more.

  Fix content.  The template dictionary, the extraction routines and the
  constraint templates are Phase 3. The reference fields here hold identifiers
  that Phase 3 will resolve. They are slots, not implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from prodpilot.blueprint import (
    NODE_EXPRESS_BLUEPRINT,
    REACT_VITE_BLUEPRINT,
    Domain,
    Priority,
    Stack,
)


class Scope(str, Enum):
    """Whether a rule is decided by one file or by several.

    Section 4 defines the two values. Scope matters to the Phase 3 loop, which
    has to know whether a fix can be applied inside a single file or has to
    reason across the project.
    """

    FILE = "file"
    CROSS_FILE = "cross_file"


class CheckType(str, Enum):
    """How a rule is evaluated, per Section 4.

    Section 4 defines exactly three check types and this enum does not add a
    fourth. The three are interpreted as follows.

    AST covers anything decided by parsing JavaScript into a syntax tree, which
    is every rule about code structure or code patterns.

    ENTROPY_SCAN covers the search for high entropy strings that indicate a
    secret, whether in working tree source or in committed history.

    FILE_EXISTENCE covers two things that look different but are the same kind
    of work: whether a required file is present, and whether a non-JavaScript
    configuration file contains a required line. Half the ruleset falls here,
    including rules such as ".gitignore excludes node_modules" and
    "package.json pins the Node engine", which read file content rather than
    merely testing for presence.

    That is a deliberate interpretation rather than a gap, and Section 4 sets
    the precedent itself. Its own P0 example, ".env.example missing", is written
    as a presence check but is only meaningful as a content check, since the
    matching blueprint requirement is that the file covers every environment key
    the code reads. A file that exists but lists nothing still fails the rule.
    Presence and simple content assertion were therefore never separate
    categories in the specification.

    The practical boundary is the parser required, not the question asked.
    Anything needing a JavaScript syntax tree is AST. Anything needing entropy
    analysis is ENTROPY_SCAN. Everything else is read as lines of text from a
    known configuration file, and that is FILE_EXISTENCE regardless of whether
    the rule asks "is this file here" or "does this file say this". Module 2.3
    implements both behaviours behind this single check type.

    Adding a fourth type would fork the vocabulary away from the Complete
    Solution Document for no gain in what the checker actually has to do.
    """

    AST = "ast"
    FILE_EXISTENCE = "file_existence"
    ENTROPY_SCAN = "entropy_scan"


class FixType(str, Enum):
    """The three fix classes from Section 4.1.

    The values carry the hyphenated spelling used throughout the Complete
    Solution Document so reports and documents agree without translation.
    """

    STATIC = "STATIC"
    DYNAMIC_PARAMETRIC = "DYNAMIC-PARAMETRIC"
    DYNAMIC_DELEGATED = "DYNAMIC-DELEGATED"


# Which reference field each fix type must populate, and only that one. STATIC
# resolves from a fixed template dictionary, DYNAMIC-PARAMETRIC from a
# deterministic extraction, DYNAMIC-DELEGATED from a constraint handed to the
# IDE agent.
REFERENCE_FIELD_FOR_FIX_TYPE: dict[FixType, str] = {
    FixType.STATIC: "fix_template_id",
    FixType.DYNAMIC_PARAMETRIC: "extraction_spec",
    FixType.DYNAMIC_DELEGATED: "constraint_template",
}

REFERENCE_FIELDS = tuple(REFERENCE_FIELD_FOR_FIX_TYPE.values())


class RuleDefinitionError(Exception):
    """Raised when a rule is declared with an inconsistent shape.

    Raised at import time rather than at audit time. A malformed rule is a
    defect in the store, and the store is data that ships with the product, so
    it should never reach a developer's project.
    """


@dataclass(frozen=True)
class Rule:
    """One production readiness rule.

    rule_id is the stable public identifier, in the domain prefixed form used
    by the fix instruction contract in Section 5.2. blueprint_item_id names the
    requirement from module 1.2 that this rule enforces, so the audit engine can
    report a failure against the blueprint without a second lookup table.
    """

    rule_id: str
    blueprint_item_id: str
    stack: Stack
    domain: Domain
    priority: Priority
    scope: Scope
    check_type: CheckType
    fix_type: FixType
    description: str
    fix_template_id: str | None = None
    extraction_spec: str | None = None
    constraint_template: str | None = None

    def __post_init__(self) -> None:
        expected = REFERENCE_FIELD_FOR_FIX_TYPE[self.fix_type]
        for field_name in REFERENCE_FIELDS:
            value = getattr(self, field_name)
            if field_name == expected:
                if not value:
                    raise RuleDefinitionError(
                        f"{self.rule_id}: fix_type {self.fix_type.value} requires "
                        f"{field_name} to be set"
                    )
            elif value is not None:
                raise RuleDefinitionError(
                    f"{self.rule_id}: fix_type {self.fix_type.value} must not set "
                    f"{field_name}"
                )

    @property
    def reference_field(self) -> str:
        """Name of the reference field this rule's fix type populates."""
        return REFERENCE_FIELD_FOR_FIX_TYPE[self.fix_type]

    @property
    def reference_value(self) -> str:
        """The identifier Phase 3 will resolve to produce the fix."""
        return getattr(self, self.reference_field)

    @property
    def is_deterministic(self) -> bool:
        """True when no free-form authorship is involved in the fix.

        Section 4.1 treats STATIC and DYNAMIC-PARAMETRIC as deterministic and
        DYNAMIC-DELEGATED as the only class carrying authorship variance. The
        proportion is reported as a measured fact, so it is computed rather than
        asserted.
        """
        return self.fix_type is not FixType.DYNAMIC_DELEGATED

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "blueprint_item_id": self.blueprint_item_id,
            "stack": self.stack.value,
            "domain": self.domain.value,
            "priority": self.priority.value,
            "scope": self.scope.value,
            "check_type": self.check_type.value,
            "fix_type": self.fix_type.value,
            "description": self.description,
            "fix_template_id": self.fix_template_id,
            "extraction_spec": self.extraction_spec,
            "constraint_template": self.constraint_template,
        }


# Shorthands, so the rule table below stays readable at a glance. The table is
# meant to be reviewed and frozen, which is easier when one rule is one line.
FILE, CROSS = Scope.FILE, Scope.CROSS_FILE
AST, EXISTS, ENTROPY = CheckType.AST, CheckType.FILE_EXISTENCE, CheckType.ENTROPY_SCAN
STATIC, PARAM, DELEG = (
    FixType.STATIC,
    FixType.DYNAMIC_PARAMETRIC,
    FixType.DYNAMIC_DELEGATED,
)
SEC, SCR, ENV = Domain.SECURITY, Domain.SECRETS, Domain.ENVIRONMENT
BLD, CON, API = Domain.BUILD, Domain.CONNECTIVITY, Domain.API
STR, OBS, GIT = Domain.STRUCTURE, Domain.OBSERVABILITY, Domain.GIT_HYGIENE
P0, P1, P2, P3, P4, P5 = (
    Priority.P0,
    Priority.P1,
    Priority.P2,
    Priority.P3,
    Priority.P4,
    Priority.P5,
)


def _rule(
    rule_id, item_id, stack, domain, priority, scope, check, fix, description, ref
):
    """Build a Rule, routing the reference identifier to the right field."""
    kwargs = {REFERENCE_FIELD_FOR_FIX_TYPE[fix]: ref}
    return Rule(
        rule_id=rule_id,
        blueprint_item_id=item_id,
        stack=stack,
        domain=domain,
        priority=priority,
        scope=scope,
        check_type=check,
        fix_type=fix,
        description=description,
        **kwargs,
    )


# Rules are declared in blueprint declaration order so this table can be diffed
# directly against blueprint.py. rule_id numbering runs per domain across both
# stacks, so an identifier stays unique and readable on its own.

NODE_EXPRESS_RULES: tuple[Rule, ...] = (
    _rule("BLD-001", "node.file.dockerfile", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, PARAM,
          "Dockerfile must be present at the project root", "ext.node.dockerfile"),
    _rule("BLD-002", "node.file.dockerignore", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, STATIC,
          ".dockerignore must be present at the project root", "tpl.node.dockerignore"),
    _rule("ENV-001", "node.file.env_example", Stack.NODE_EXPRESS, ENV, P0, CROSS, AST, PARAM,
          ".env.example must cover every environment key the code reads", "ext.node.env_keys"),
    _rule("GIT-001", "node.file.gitignore", Stack.NODE_EXPRESS, GIT, P5, FILE, EXISTS, STATIC,
          ".gitignore must be present at the project root", "tpl.node.gitignore"),
    _rule("BLD-003", "node.file.ci_workflow", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, PARAM,
          "a GitHub Actions workflow must be present under .github/workflows", "ext.node.ci_workflow"),

    _rule("GIT-002", "node.config.gitignore_node_modules", Stack.NODE_EXPRESS, GIT, P5, FILE, EXISTS, STATIC,
          ".gitignore must exclude node_modules", "tpl.node.gitignore_node_modules"),
    _rule("SCR-001", "node.config.gitignore_env", Stack.NODE_EXPRESS, SCR, P0, FILE, EXISTS, STATIC,
          ".gitignore must exclude .env and .env.production", "tpl.node.gitignore_env"),
    _rule("BLD-004", "node.config.start_script", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, PARAM,
          "package.json must declare a start script that runs without a dev watcher", "ext.node.start_script"),
    _rule("BLD-005", "node.config.engine_pin", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, STATIC,
          "package.json must pin the Node engine to 20 LTS", "tpl.node.engine_pin"),
    _rule("BLD-006", "node.config.multistage_build", Stack.NODE_EXPRESS, BLD, P1, FILE, EXISTS, PARAM,
          "the Dockerfile must use a multi-stage build", "ext.node.multistage_build"),
    _rule("SEC-001", "node.config.non_root_user", Stack.NODE_EXPRESS, SEC, P0, FILE, EXISTS, STATIC,
          "the Dockerfile must run the process as a non-root user", "tpl.node.non_root_user"),

    _rule("SEC-002", "node.code.helmet_registered", Stack.NODE_EXPRESS, SEC, P0, FILE, AST, STATIC,
          "helmet must be registered before any route definition", "tpl.node.helmet_registered"),
    _rule("SEC-003", "node.code.cors_from_env", Stack.NODE_EXPRESS, SEC, P0, FILE, AST, STATIC,
          "the CORS origin must come from an environment variable, not a wildcard", "tpl.node.cors_from_env"),
    _rule("SEC-004", "node.code.csp_headers", Stack.NODE_EXPRESS, SEC, P0, FILE, AST, STATIC,
          "Content Security Policy and HTTPS redirect headers must be set", "tpl.node.csp_headers"),
    _rule("SCR-002", "node.code.no_hardcoded_secrets", Stack.NODE_EXPRESS, SCR, P0, CROSS, ENTROPY, PARAM,
          "no API key, token or password literal may appear in source", "ext.node.secret_to_env_var"),
    _rule("ENV-002", "node.code.port_from_env", Stack.NODE_EXPRESS, ENV, P0, FILE, AST, STATIC,
          "the listening port must be read from the environment", "tpl.node.port_from_env"),
    _rule("CON-001", "node.code.db_credentials_from_env", Stack.NODE_EXPRESS, CON, P1, FILE, AST, PARAM,
          "database credentials must be read from the environment", "ext.node.db_credentials"),
    _rule("CON-002", "node.code.connection_pooling", Stack.NODE_EXPRESS, CON, P1, FILE, AST, PARAM,
          "database access must use a connection pool", "ext.node.connection_pool"),
    _rule("API-001", "node.code.rate_limiting", Stack.NODE_EXPRESS, API, P2, FILE, AST, STATIC,
          "public routes must be rate limited", "tpl.node.rate_limiting"),
    _rule("API-002", "node.code.api_versioning", Stack.NODE_EXPRESS, API, P2, CROSS, AST, PARAM,
          "routes must be mounted under a versioned prefix", "ext.node.api_version_prefix"),
    _rule("API-003", "node.code.error_middleware", Stack.NODE_EXPRESS, API, P2, FILE, AST, STATIC,
          "a centralised error handler must return a consistent shape and leak no stack traces", "tpl.node.error_middleware"),
    _rule("STR-001", "node.code.service_layer", Stack.NODE_EXPRESS, STR, P3, CROSS, AST, DELEG,
          "database calls must live in a service layer rather than inside controllers", "con.node.service_layer"),
    _rule("STR-002", "node.code.thin_route_handlers", Stack.NODE_EXPRESS, STR, P3, CROSS, AST, DELEG,
          "business logic must live in services rather than inline in route handlers", "con.node.thin_route_handlers"),
    _rule("OBS-001", "node.code.health_endpoint", Stack.NODE_EXPRESS, OBS, P4, FILE, AST, STATIC,
          "a health check endpoint must be exposed", "tpl.node.health_endpoint"),
    _rule("OBS-002", "node.code.structured_logging", Stack.NODE_EXPRESS, OBS, P4, FILE, AST, STATIC,
          "structured logging must be configured rather than bare console output", "tpl.node.structured_logging"),
    _rule("OBS-003", "node.code.monitoring_hooks", Stack.NODE_EXPRESS, OBS, P4, FILE, AST, STATIC,
          "the process must expose monitoring hooks the hosting platform can scrape", "tpl.node.monitoring_hooks"),
    _rule("OBS-004", "node.code.graceful_shutdown", Stack.NODE_EXPRESS, OBS, P4, FILE, AST, STATIC,
          "SIGTERM must be handled so the server drains connections before exit", "tpl.node.graceful_shutdown"),
    # GIT-003 is delegated even though it is not a structure rule. Section 4.1
    # introduces DYNAMIC-DELEGATED with "structural rules such as the P3
    # domain", which names the common case as an example rather than fixing the
    # boundary at that domain. The test for delegation is whether the fix needs
    # judgment about this specific codebase, and removing a secret from
    # committed history does. It rewrites published commits, so the correct
    # action depends on how far back the secret goes, whether the branch is
    # shared, and whether the credential has already been rotated. There is no
    # template that is correct for every repository, and nothing to extract
    # mechanically, which rules out STATIC and DYNAMIC-PARAMETRIC. The agent
    # holds the repository context needed to decide, so it authors the change
    # under a constraint and the result is re-verified like any other fix.
    _rule("GIT-003", "node.history.no_committed_secrets", Stack.NODE_EXPRESS, GIT, P5, CROSS, ENTROPY, DELEG,
          "no secret may appear anywhere in the committed Git history", "con.node.history_secret_removal"),
)


REACT_VITE_RULES: tuple[Rule, ...] = (
    _rule("BLD-007", "react.file.dockerfile", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, PARAM,
          "Dockerfile must be present at the project root", "ext.react.dockerfile"),
    _rule("BLD-008", "react.file.dockerignore", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, STATIC,
          ".dockerignore must be present at the project root", "tpl.react.dockerignore"),
    _rule("BLD-009", "react.file.nginx_conf", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, STATIC,
          "an nginx configuration must be present for serving the built application", "tpl.react.nginx_conf"),
    _rule("ENV-003", "react.file.env_example", Stack.REACT_VITE, ENV, P0, CROSS, AST, PARAM,
          ".env.example must cover every VITE_ key the code reads", "ext.react.env_keys"),
    _rule("GIT-004", "react.file.gitignore", Stack.REACT_VITE, GIT, P5, FILE, EXISTS, STATIC,
          ".gitignore must be present at the project root", "tpl.react.gitignore"),
    _rule("BLD-010", "react.file.ci_workflow", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, PARAM,
          "a GitHub Actions workflow must be present under .github/workflows", "ext.react.ci_workflow"),

    _rule("GIT-005", "react.config.gitignore_node_modules", Stack.REACT_VITE, GIT, P5, FILE, EXISTS, STATIC,
          ".gitignore must exclude node_modules", "tpl.react.gitignore_node_modules"),
    _rule("GIT-006", "react.config.gitignore_dist", Stack.REACT_VITE, GIT, P5, FILE, EXISTS, STATIC,
          ".gitignore must exclude the dist build output", "tpl.react.gitignore_dist"),
    _rule("SCR-003", "react.config.gitignore_env", Stack.REACT_VITE, SCR, P0, FILE, EXISTS, STATIC,
          ".gitignore must exclude .env and .env.production", "tpl.react.gitignore_env"),
    _rule("BLD-011", "react.config.build_script", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, STATIC,
          "package.json must declare a build script that produces the production bundle", "tpl.react.build_script"),
    _rule("BLD-012", "react.config.multistage_build", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, PARAM,
          "the Dockerfile must build in one stage and serve the assets from a static image", "ext.react.multistage_build"),
    _rule("BLD-013", "react.config.spa_fallback", Stack.REACT_VITE, BLD, P1, FILE, EXISTS, STATIC,
          "nginx must fall back to index.html so client side routes resolve on refresh", "tpl.react.spa_fallback"),
    _rule("SEC-005", "react.config.non_root_user", Stack.REACT_VITE, SEC, P0, FILE, EXISTS, STATIC,
          "the Dockerfile must run the server process as a non-root user", "tpl.react.non_root_user"),
    _rule("OBS-005", "react.config.health_path", Stack.REACT_VITE, OBS, P4, FILE, EXISTS, STATIC,
          "nginx must serve a health path that does not load the application bundle", "tpl.react.health_path"),
    _rule("OBS-006", "react.config.access_logging", Stack.REACT_VITE, OBS, P4, FILE, EXISTS, STATIC,
          "nginx access and error logging must write to the container log stream", "tpl.react.access_logging"),

    _rule("ENV-004", "react.code.api_url_from_env", Stack.REACT_VITE, ENV, P0, CROSS, AST, PARAM,
          "the API base URL must be read from import.meta.env rather than hardcoded", "ext.react.api_url_env_var"),
    _rule("ENV-005", "react.code.no_hardcoded_backend_urls", Stack.REACT_VITE, ENV, P0, CROSS, AST, PARAM,
          "no backend host literal may appear in source", "ext.react.backend_url_env_var"),
    _rule("SCR-004", "react.code.no_hardcoded_secrets", Stack.REACT_VITE, SCR, P0, CROSS, ENTROPY, PARAM,
          "no API key or token literal may appear in source", "ext.react.secret_to_env_var"),
    _rule("SEC-006", "react.code.security_headers", Stack.REACT_VITE, SEC, P0, FILE, EXISTS, STATIC,
          "the serving layer must set Content Security Policy and related security headers", "tpl.react.security_headers"),
    _rule("STR-003", "react.code.error_boundary", Stack.REACT_VITE, STR, P3, CROSS, AST, DELEG,
          "an error boundary must wrap the root of the component tree", "con.react.error_boundary"),
    _rule("STR-004", "react.code.catch_all_route", Stack.REACT_VITE, STR, P3, FILE, AST, DELEG,
          "the client router must handle unmatched routes with a catch all", "con.react.catch_all_route"),
    # GIT-007 is the React counterpart of GIT-003 and is delegated for the same
    # reason. See the comment on GIT-003 for why a git-hygiene rule qualifies
    # for delegated treatment despite Section 4.1 framing that classification
    # around structural rules.
    _rule("GIT-007", "react.history.no_committed_secrets", Stack.REACT_VITE, GIT, P5, CROSS, ENTROPY, DELEG,
          "no secret may appear anywhere in the committed Git history", "con.react.history_secret_removal"),
)


ALL_RULES: tuple[Rule, ...] = NODE_EXPRESS_RULES + REACT_VITE_RULES

_RULES_BY_STACK: dict[Stack, tuple[Rule, ...]] = {
    Stack.NODE_EXPRESS: NODE_EXPRESS_RULES,
    Stack.REACT_VITE: REACT_VITE_RULES,
}

_RULES_BY_ID: dict[str, Rule] = {rule.rule_id: rule for rule in ALL_RULES}

_BLUEPRINTS_BY_STACK = {
    Stack.NODE_EXPRESS: NODE_EXPRESS_BLUEPRINT,
    Stack.REACT_VITE: REACT_VITE_BLUEPRINT,
}


def rules_for_stack(stack: Stack) -> tuple[Rule, ...]:
    """Return the rules that apply to a stack.

    Returns an empty tuple for Stack.UNRECOGNIZED, which has no blueprint and
    therefore nothing to audit against.
    """
    return _RULES_BY_STACK.get(stack, ())


def get_rule(rule_id: str) -> Rule | None:
    """Return one rule by its identifier, or None when it does not exist."""
    return _RULES_BY_ID.get(rule_id)


def rules_by_priority(stack: Stack) -> tuple[Rule, ...]:
    """Return a stack's rules ordered P0 first.

    The Phase 3 loop consumes audit output as a priority ordered queue, so the
    ordering is defined here once rather than at each call site. Rules of equal
    priority keep their declaration order, which follows the blueprint.
    """
    order = {priority: index for index, priority in enumerate(Priority)}
    return tuple(sorted(rules_for_stack(stack), key=lambda r: order[r.priority]))


def determinism_ratio() -> float:
    """Proportion of the ruleset that needs no free-form authorship.

    Section 4.1 requires this to be a measured and reported fact rather than an
    assumption, and Section 13 lists it as a success metric.
    """
    if not ALL_RULES:
        return 0.0
    deterministic = sum(1 for rule in ALL_RULES if rule.is_deterministic)
    return deterministic / len(ALL_RULES)


def blueprint_item_ids(stack: Stack) -> frozenset[str]:
    """Every blueprint item identifier declared for a stack."""
    blueprint = _BLUEPRINTS_BY_STACK.get(stack)
    if blueprint is None:
        return frozenset()
    return frozenset(item.item_id for item in blueprint.items)
