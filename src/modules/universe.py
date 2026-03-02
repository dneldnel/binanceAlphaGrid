from __future__ import annotations

from core.models import AppConfig, SymbolConfig


class UniverseSelector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def select(self) -> list[SymbolConfig]:
        include = set(self.config.universe.include_symbols)
        exclude = set(self.config.universe.exclude_symbols)

        selected: list[SymbolConfig] = []
        for symbol in self.config.symbols.values():
            if not symbol.enabled:
                continue
            if include and symbol.name not in include:
                continue
            if symbol.name in exclude:
                continue
            selected.append(symbol)

        selected.sort(key=lambda item: item.name)
        return selected[: self.config.universe.max_symbols]
