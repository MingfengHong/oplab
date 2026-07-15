from oplab.retrieval.base import PassageCandidate, SourceCandidate


class StubSearch:
    async def search(self, query: str, limit: int) -> list[SourceCandidate]:
        if "limitations" in query:
            return [
                SourceCandidate(
                    source_type="academic",
                    title="Boundary conditions and null results",
                    uri="https://example.test/counter",
                    doi="10.1000/counter",
                    authors=["R. Skeptic"],
                    published_at="2025",
                    content=(
                        "Evidence from small projects shows that the association weakens when "
                        "governance continuity and contributor redundancy are controlled."
                    ),
                    passages=[
                        PassageCandidate(
                            locator="abstract:1",
                            text=(
                                "Evidence from small projects shows that the association weakens "
                                "when governance continuity and contributor redundancy "
                                "are controlled."
                            ),
                        )
                    ],
                    quality={"provider": "test", "primary_source": True},
                )
            ]
        return [
            SourceCandidate(
                source_type="academic",
                title="Maintainer diversity and project resilience",
                uri="https://example.test/support",
                doi="10.1000/support",
                authors=["L. Evidence"],
                published_at="2024",
                content=(
                    "Maintainer diversity is associated with faster recovery after contributor "
                    "loss in a longitudinal sample of open-source projects."
                ),
                passages=[
                    PassageCandidate(
                        locator="abstract:1",
                        text=(
                            "Maintainer diversity is associated with faster recovery after "
                            "contributor loss in a longitudinal sample of open-source projects."
                        ),
                    )
                ],
                quality={"provider": "test", "primary_source": True},
            )
        ]
