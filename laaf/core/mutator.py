"""
Mutation Engine
===============
On stage breakthrough, generates variants of the winning payload through
four mutation strategies mirroring real adversarial escalation.

Paper §4.6 & §6.2 — LAAF (Atta et al., 2026)
"""

from __future__ import annotations

import random
from typing import Optional

from laaf.generators.payload_generator import Payload, PayloadGenerator
from laaf.taxonomy.base import Category, get_registry


class MutationEngine:
    """
    Four mutation strategies:
    1. Encoding mutation   — same instruction, different encoding
    2. Reframe mutation    — same encoding, different semantic wrapper
    3. Trigger mutation    — same technique, different trigger keyword
    4. Compound mutation   — escalated multi-layer combination
    """

    def __init__(self) -> None:
        self._registry = get_registry()
        self._generator = PayloadGenerator()

    def mutate(
        self,
        seed: Payload,
        strategy: Optional[str] = None,
        count: int = 10,
    ) -> list[Payload]:
        """
        Generate `count` mutations of the seed payload.
        strategy: "encoding" | "reframe" | "trigger" | "compound" | None (auto)
        """
        if strategy is None:
            strategy = random.choice(["encoding", "reframe", "trigger", "compound"])

        mutator = {
            "encoding": self._encoding_mutation,
            "reframe": self._reframe_mutation,
            "trigger": self._trigger_mutation,
            "compound": self._compound_mutation,
        }.get(strategy, self._encoding_mutation)

        results: list[Payload] = []
        for _ in range(count):
            mutated = mutator(seed)
            if mutated:
                results.append(mutated)
        return results

    def _encoding_mutation(self, seed: Payload) -> Optional[Payload]:
        """Same instruction, different encoding technique."""
        encodings = self._registry.by_category(Category.ENCODING)
        technique = random.choice(encodings)
        content = technique(seed.raw_instruction)
        return self._clone(seed, content, technique.id, technique.name, technique.category.value)

    def _reframe_mutation(self, seed: Payload) -> Optional[Payload]:
        """Same encoding, different semantic wrapper around instruction."""
        semantics = self._registry.by_category(Category.SEMANTIC)
        technique = random.choice(semantics)
        content = technique(seed.raw_instruction)
        return self._clone(seed, content, technique.id, technique.name, technique.category.value)

    def _trigger_mutation(self, seed: Payload) -> Optional[Payload]:
        """Same technique applied with a different trigger keyword."""
        tech = self._registry.get(seed.technique_id)
        if tech is None:
            return None
        content = tech(seed.raw_instruction)
        # Swap trigger keyword
        keywords = [
            "payment", "budget", "report", "document", "transfer",
            "approve", "review", "confirm", "export", "audit",
        ]
        new_keyword = random.choice([k for k in keywords if k != seed.trigger_keyword])
        content = content.replace(str(seed.trigger_keyword), new_keyword)
        return self._clone(seed, content, seed.technique_id, seed.technique_name, seed.category, new_keyword)

    def _compound_mutation(self, seed: Payload) -> Optional[Payload]:
        """Escalate to a layered combination incorporating successful elements."""
        layered = self._registry.by_category(Category.LAYERED)
        technique = random.choice(layered)
        content = technique(seed.raw_instruction)
        return self._clone(seed, content, technique.id, technique.name, technique.category.value)

    @staticmethod
    def _clone(
        seed: Payload,
        content: str,
        tech_id: str,
        tech_name: str,
        category: str,
        trigger_keyword: Optional[str] = None,
    ) -> Payload:
        import hashlib
        pid = f"MUT-{hashlib.sha256(content.encode()).hexdigest()[:8].upper()}"
        return Payload(
            id=pid,
            raw_instruction=seed.raw_instruction,
            technique_id=tech_id,
            technique_name=tech_name,
            category=category,
            content=content,
            trigger_keyword=trigger_keyword or seed.trigger_keyword,
            attack_vector=seed.attack_vector,
            stage=seed.stage,
            is_seed=False,
        )

    def select_strategy(self, consecutive_blocks: int) -> str:
        """
        Adaptive strategy selection based on consecutive block count (paper §6.2):
        c < 10  → seed variation (encoding mutation)
        10 ≤ c < 20 → encoding mutation
        c ≥ 20  → compound mutation
        """
        if consecutive_blocks < 10:
            return "encoding"
        elif consecutive_blocks < 20:
            return "reframe"
        else:
            return "compound"
