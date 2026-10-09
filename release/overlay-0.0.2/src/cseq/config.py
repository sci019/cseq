from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib


@dataclass(frozen=True, slots=True)
class CpuPreprocessorRule:
    when_defined: str
    assign_group: str | None = None
    assign_cpu: str | None = None


@dataclass(frozen=True, slots=True)
class CpuPathRule:
    glob: str
    assign_group: str | None = None
    assign_cpu: str | None = None


@dataclass(frozen=True, slots=True)
class CpuOverrideRule:
    file: str
    assign_group: str | None = None
    assign_cpu: str | None = None


@dataclass(slots=True)
class CseqConfig:
    path: Path | None = None
    cpu_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preprocessor_rules: list[CpuPreprocessorRule] = field(default_factory=list)
    path_rules: list[CpuPathRule] = field(default_factory=list)
    override_rules: list[CpuOverrideRule] = field(default_factory=list)
    configurations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    plugins_enabled: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "CseqConfig":
        return cls()

    @classmethod
    def load(cls, path: str | Path) -> "CseqConfig":
        p = Path(path)
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        cfg = cls(path=p.resolve())
        for name, value in (data.get("cpu_groups") or {}).items():
            cfg.cpu_groups[str(name)] = tuple(str(x) for x in (value or {}).get("members", []))
        cpu_rules = data.get("cpu_rules") or {}
        for r in cpu_rules.get("preprocessor", []) or []:
            cfg.preprocessor_rules.append(CpuPreprocessorRule(str(r["when_defined"]), _opt(r, "assign_group"), _opt(r, "assign_cpu")))
        for r in cpu_rules.get("path", []) or []:
            cfg.path_rules.append(CpuPathRule(str(r["glob"]), _opt(r, "assign_group"), _opt(r, "assign_cpu")))
        for r in cpu_rules.get("override", []) or []:
            cfg.override_rules.append(CpuOverrideRule(str(r["file"]), _opt(r, "assign_group"), _opt(r, "assign_cpu")))
        for name, value in (data.get("configurations") or {}).items():
            cfg.configurations[str(name)] = tuple(str(x) for x in (value or {}).get("defines", []))
        plugins = data.get("plugins") or {}
        cfg.plugins_enabled = tuple(str(x) for x in (plugins.get("enabled") or []))
        return cfg

    def defines_for(self, configuration: str | None, extra_defines: tuple[str, ...] = ()) -> tuple[str, ...]:
        merged: list[str] = []
        if configuration:
            if configuration not in self.configurations:
                raise ValueError(f"unknown configuration: {configuration}")
            merged.extend(self.configurations[configuration])
        merged.extend(extra_defines)
        return tuple(dict.fromkeys(merged))


def _opt(mapping: dict, key: str) -> str | None:
    value = mapping.get(key)
    return str(value) if value is not None else None


def validate_config(config: CseqConfig) -> list[dict[str, str]]:
    """Return structured configuration diagnostics without mutating the config."""
    issues: list[dict[str, str]] = []
    known_groups = set(config.cpu_groups)
    for kind, rules in (
        ("preprocessor", config.preprocessor_rules),
        ("path", config.path_rules),
        ("override", config.override_rules),
    ):
        seen: set[tuple[str, str | None, str | None]] = set()
        for rule in rules:
            selector = getattr(rule, "when_defined", None) or getattr(rule, "glob", None) or getattr(rule, "file", None) or ""
            key = (str(selector), getattr(rule, "assign_group", None), getattr(rule, "assign_cpu", None))
            if key in seen:
                issues.append({"severity": "warning", "code": "DUPLICATE_RULE", "message": f"duplicate {kind} rule: {selector}"})
            seen.add(key)
            group = getattr(rule, "assign_group", None)
            cpu = getattr(rule, "assign_cpu", None)
            if group and group not in known_groups:
                issues.append({"severity": "error", "code": "UNKNOWN_CPU_GROUP", "message": f"{kind} rule references unknown group: {group}"})
            if group and cpu:
                issues.append({"severity": "error", "code": "AMBIGUOUS_ASSIGNMENT", "message": f"{kind} rule assigns both group and cpu: {selector}"})
            if not group and not cpu:
                issues.append({"severity": "error", "code": "MISSING_ASSIGNMENT", "message": f"{kind} rule has no assignment: {selector}"})
    for name, members in config.cpu_groups.items():
        if not members:
            issues.append({"severity": "warning", "code": "EMPTY_CPU_GROUP", "message": f"CPU group has no members: {name}"})
    return issues


def default_config_text(*, compile_commands_present: bool = False) -> str:
    lines = [
        '# cseq configuration',
        '# Generated conservatively. Company-specific CPU/RTOS semantics are not guessed.',
        '',
        '[project]',
        'entries = ["main"]',
        '',
        '[parser]',
        'dialect = "auto"',
        f'compile_commands = "{"auto" if compile_commands_present else "none"}"',
        'tolerant = true',
        '',
        '[analysis]',
        'indirect_calls = true',
        '',
        '[sequence]',
        'external_calls = "self"',
        '',
        '[html]',
        'mode = "auto"',
        '',
        '[plugins]',
        'enabled = []',
        '',
        '# Example only; uncomment and rename if needed.',
        '# [cpu_groups.CPU_GROUP_A]',
        '# members = ["CPU_A1", "CPU_A2"]',
        '',
    ]
    return '\n'.join(lines)
