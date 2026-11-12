"""Synthetic examples for an offline presentation; never used by live mode."""


class Demo:
    def search(self, query, today, limit):
        return [
            {"title": "DEMO: Musical fountain design and installation",
             "url": "https://example.com/demo/fountain",
             "content": "Synthetic tender: design, supply and commission a musical fountain. Deadline not supplied."},
            {"title": "DEMO: Tourism precinct development",
             "url": "https://example.com/demo/tourism",
             "content": "Synthetic tender: tourism precinct with a multimedia water show subsystem. Full scope requires review."},
            {"title": "DEMO: Irrigation pump supply",
             "url": "https://example.com/demo/pumps",
             "content": "Synthetic tender for agricultural irrigation pumps. No fountain or entertainment scope."},
            {"title": "DEMO: Fountain manufacturer catalogue",
             "url": "https://example.com/demo/catalogue",
             "content": "Synthetic product catalogue for decorative fountains. Not a procurement notice."},
        ][:limit]

    def complete(self, system, data):
        if "candidate" not in data:
            return {"groups": {}}
        candidate = data["candidate"]
        title = candidate["title"].lower()
        score = .95 if 'design and installation' in title else .58 if 'tourism' in title else .1
        return {"score": score, "is_tender": 'catalogue' not in title,
                "reason": "Synthetic example: " + (
                    "direct capability match; eligibility and deadline need checking." if score > .7 else
                    "partial subsystem opportunity; inspect full scope." if score > .4 else
                    "no relevant procurement opportunity demonstrated."),
                "evidence": candidate["snippets"][0], "deadline": None}
