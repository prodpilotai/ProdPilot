"""Stack detection for Layer 0.

Scope is Phase 1 module 1.2. Reads the file structure and package.json of a
target project and classifies it as one of the two stacks ProdPilot supports in
v1, or as unrecognized.

Detection is deterministic. It reads declared dependencies and looks for
framework config files. It runs no model and makes no network call.

Section 9 of the Complete Solution Document limits v1 to Node.js 20 LTS with
Express and React 18 with Vite 5. Anything else is unrecognized by design, not
by omission.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot.blueprint import ProductionBlueprint, Stack, get_blueprint

logger = logging.getLogger(__name__)

PACKAGE_MANIFEST = "package.json"

# Vite writes its config with any of these extensions depending on the module
# system the project uses.
VITE_CONFIG_NAMES = (
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "vite.config.ts",
    "vite.config.mts",
    "vite.config.cts",
)

EXPRESS_PACKAGE = "express"
REACT_PACKAGES = ("react", "react-dom")
VITE_PACKAGE = "vite"


class DetectionError(Exception):
    """Raised when the target path cannot be inspected at all.

    This covers caller mistakes such as a path that does not exist or is not a
    directory. A project that exists but matches no supported stack is not an
    error, it is an unrecognized result.
    """


@dataclass(frozen=True)
class DetectionEvidence:
    """What detection actually observed, so a result can be audited."""

    manifest_found: bool = False
    manifest_readable: bool = False
    declared_dependencies: tuple[str, ...] = field(default_factory=tuple)
    matched_packages: tuple[str, ...] = field(default_factory=tuple)
    config_files_found: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_found": self.manifest_found,
            "manifest_readable": self.manifest_readable,
            "declared_dependency_count": len(self.declared_dependencies),
            "matched_packages": list(self.matched_packages),
            "config_files_found": list(self.config_files_found),
        }


@dataclass(frozen=True)
class DetectionResult:
    """The outcome of inspecting one project directory."""

    project_path: str
    stack: Stack
    reason: str
    evidence: DetectionEvidence

    @property
    def is_supported(self) -> bool:
        return self.stack is not Stack.UNRECOGNIZED

    def blueprint(self) -> ProductionBlueprint | None:
        """Return the production blueprint matching the detected stack."""
        return get_blueprint(self.stack)

    def to_dict(self) -> dict[str, object]:
        return {
            "project_path": self.project_path,
            "stack": self.stack.value,
            "is_supported": self.is_supported,
            "reason": self.reason,
            "evidence": self.evidence.to_dict(),
        }


def _read_manifest(manifest_path: Path) -> dict | None:
    """Parse package.json.

    Returns None when the file cannot be parsed. A malformed manifest is a
    property of the project under inspection, not a failure of ProdPilot, so it
    resolves to an unrecognized result rather than an exception.
    """
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot read %s: %s", manifest_path, exc)
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "malformed %s at line %s: %s", PACKAGE_MANIFEST, exc.lineno, exc.msg
        )
        return None

    if not isinstance(parsed, dict):
        logger.warning("%s does not contain a JSON object", PACKAGE_MANIFEST)
        return None

    return parsed


def _declared_dependencies(manifest: dict) -> tuple[str, ...]:
    """Collect every package name the manifest declares.

    Runtime and development dependencies are both considered. Vite and the React
    plugin are conventionally development dependencies while react itself is a
    runtime dependency, so restricting to one section would miss real projects.
    """
    names: list[str] = []
    for section in ("dependencies", "devDependencies"):
        block = manifest.get(section)
        if isinstance(block, dict):
            names.extend(str(name) for name in block)
    return tuple(sorted(set(names)))


def _find_config_files(project_path: Path) -> tuple[str, ...]:
    """Return the framework config files present at the project root."""
    return tuple(
        name for name in VITE_CONFIG_NAMES if (project_path / name).is_file()
    )


def detect_stack(project_path: str | Path) -> DetectionResult:
    """Classify a project directory as one of the supported stacks.

    Raises DetectionError when the path does not exist or is not a directory.
    """
    path = Path(project_path).expanduser()
    if not path.exists():
        raise DetectionError(f"path does not exist: {path}")
    if not path.is_dir():
        raise DetectionError(f"path is not a directory: {path}")

    resolved = path.resolve()
    manifest_path = resolved / PACKAGE_MANIFEST

    if not manifest_path.is_file():
        logger.info("no %s under %s", PACKAGE_MANIFEST, resolved)
        return DetectionResult(
            project_path=str(resolved),
            stack=Stack.UNRECOGNIZED,
            reason=f"no {PACKAGE_MANIFEST} found at the project root",
            evidence=DetectionEvidence(
                config_files_found=_find_config_files(resolved)
            ),
        )

    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return DetectionResult(
            project_path=str(resolved),
            stack=Stack.UNRECOGNIZED,
            reason=(
                f"{PACKAGE_MANIFEST} is present but could not be parsed as a "
                "JSON object"
            ),
            evidence=DetectionEvidence(manifest_found=True, manifest_readable=False),
        )

    dependencies = _declared_dependencies(manifest)
    config_files = _find_config_files(resolved)
    dependency_set = set(dependencies)

    has_express = EXPRESS_PACKAGE in dependency_set
    has_react = all(pkg in dependency_set for pkg in REACT_PACKAGES)
    has_vite = VITE_PACKAGE in dependency_set or bool(config_files)

    matched: list[str] = []
    if has_express:
        matched.append(EXPRESS_PACKAGE)
    if has_react:
        matched.extend(REACT_PACKAGES)
    if VITE_PACKAGE in dependency_set:
        matched.append(VITE_PACKAGE)

    evidence = DetectionEvidence(
        manifest_found=True,
        manifest_readable=True,
        declared_dependencies=dependencies,
        matched_packages=tuple(matched),
        config_files_found=config_files,
    )

    is_react_vite = has_react and has_vite

    if is_react_vite and has_express:
        # Both stacks match. The two blueprints differ substantially, one targets
        # a Node runtime and the other static assets behind nginx, so guessing
        # would pick the wrong production shape. Fail closed and say why.
        logger.info(
            "ambiguous stack under %s, express and react with vite both present",
            resolved,
        )
        return DetectionResult(
            project_path=str(resolved),
            stack=Stack.UNRECOGNIZED,
            reason=(
                "ambiguous project, express and react with vite are both "
                "declared. Split the frontend and backend into separate project "
                "roots so each can be matched to its own blueprint"
            ),
            evidence=evidence,
        )

    if is_react_vite:
        logger.info("detected react with vite under %s", resolved)
        return DetectionResult(
            project_path=str(resolved),
            stack=Stack.REACT_VITE,
            reason="react and react-dom are declared alongside vite",
            evidence=evidence,
        )

    if has_express:
        logger.info("detected node with express under %s", resolved)
        return DetectionResult(
            project_path=str(resolved),
            stack=Stack.NODE_EXPRESS,
            reason="express is declared as a project dependency",
            evidence=evidence,
        )

    if has_react:
        reason = (
            "react is declared but vite is not. Only React with Vite is "
            "supported in v1"
        )
    elif has_vite:
        reason = (
            "vite is present but react is not. Only React with Vite is "
            "supported in v1"
        )
    else:
        reason = f"{PACKAGE_MANIFEST} declares neither express nor react with vite"

    logger.info("unrecognized stack under %s: %s", resolved, reason)
    return DetectionResult(
        project_path=str(resolved),
        stack=Stack.UNRECOGNIZED,
        reason=reason,
        evidence=evidence,
    )
