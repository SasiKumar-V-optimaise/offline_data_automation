# src/domains/rm/neon_mapper.py

import pandas as pd
from collections import defaultdict
from typing import Iterator


class RMNeonMapper:
    def __init__(self, material_lookup: dict[str, int], category_map: dict, logger=None):
        self.material_lookup = material_lookup
        self.category_map = category_map
        self.logger = logger

    def _parse(self, col: str) -> tuple[str, str] | None:
        col_l = col.lower().strip()
        for pattern in self.category_map:
            if col_l.startswith(pattern + " "):
                return pattern, col_l[len(pattern) + 1:]
        return None

    def iter_table_dfs(self, df: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame]]:
        groups: dict[tuple, list] = defaultdict(list)

        for col in (c for c in df.columns if c != "date"):
            parsed = self._parse(col)
            if not parsed:
                continue
            pattern, prop = parsed
            m = self.category_map[pattern]
            groups[(m["table"], m["db_material"])].append((col, prop))

        for (table, db_material), cols_props in groups.items():
            material_id = self.material_lookup.get(db_material.upper())
            if material_id is None:
                if self.logger:
                    self.logger.warning(f"Material not found in DB: {db_material}")
                continue

            col_names = [c for c, _ in cols_props]
            out = df[["date"] + col_names].copy()
            out = out.rename(columns={"date": "date_time", **{c: p for c, p in cols_props}})
            out["material_id"] = material_id

            prop_cols = [c for c in out.columns if c not in {"date_time", "material_id"}]
            yield table, out[["date_time"] + prop_cols + ["material_id"]]
