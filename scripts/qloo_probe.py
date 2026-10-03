"""Check how well Qloo covers era- and region-specific taste for a few personas.

    uv run scripts/qloo_probe.py            # all personas
    uv run scripts/qloo_probe.py --raw      # also dump raw search hits
"""

import argparse
import asyncio
import json

from dotenv import load_dotenv

from kin.qloo import QlooClient
from kin.reminiscence import Person, build_set

PERSONAS = [
    Person("Asha", 1948, "Kolkata", ["Satyajit Ray", "Hemanta Mukherjee"]),
    Person("Ramesh", 1952, "Chennai", ["Ilaiyaraaja", "Sivaji Ganesan"]),
    Person("Margaret", 1946, "Liverpool", ["The Beatles"]),
    Person("Carmen", 1950, "Mexico City", ["Pedro Infante"]),
]


async def main(raw: bool) -> None:
    load_dotenv()
    async with QlooClient() as qloo:
        for person in PERSONAS:
            result = await build_set(qloo, person)
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
            if raw:
                for fav in person.favourites:
                    hits = await qloo.search(fav, take=3)
                    print(f"  search {fav!r}: {[(h.name, h.types) for h in hits]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", action="store_true")
    asyncio.run(main(parser.parse_args().raw))
