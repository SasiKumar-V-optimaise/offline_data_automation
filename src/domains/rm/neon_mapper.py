# src/domains/rm/neon_mapper.py

import pandas as pd
import re
from typing import Iterator


class RMNeonMapper:
    """
    Maps RM processed data to Neon DB format.
    
    The rename_fields in rm.yaml have format: 
    "fuel coke online ash" -> category: fuel, material: coke online, property: ash
    
    Pattern: {category} {material} {property}
    """

    def __init__(self, material_lookup: dict[str, int], logger=None):
        self.material_lookup = material_lookup
        self.logger = logger
        
        # Map from rename pattern to DB table and material name
        self.category_map = self._build_category_map()

    def _build_category_map(self) -> dict:
        """
        Build mapping from column pattern to DB table and material name.
        """
        return {
            # Fuel - coke online
            "fuel coke online": {
                "table": "fuel_chemistry",
                "db_material": "COKE ONLINE",
            },
            # Fuel - coke offline
            "fuel coke offline": {
                "table": "fuel_chemistry",
                "db_material": "COKE OFFLINE",
            },
            # Fuel - nut coke online
            "fuel nut coke online": {
                "table": "fuel_chemistry",
                "db_material": "NUT COKE ONLINE",
            },
            # Fuel - nut coke offline
            "fuel nut coke offline": {
                "table": "fuel_chemistry",
                "db_material": "NUT COKE OFFLINE",
            },
            # Fuel - pci
            "fuel pci": {
                "table": "fuel_chemistry",
                "db_material": "PCI",
            },
            # Flux 1
            "flux 1": {
                "table": "flux_chemistry",
                "db_material": "FLUX 1",
            },
            # Flux 2
            "flux 2": {
                "table": "flux_chemistry",
                "db_material": "FLUX 2",
            },
            # Flux 3
            "flux 3": {
                "table": "flux_chemistry",
                "db_material": "FLUX 3",
            },
            # Sinter
            "sinter sp 02 online": {
                "table": "sinter_chemistry",
                "db_material": "SINTER SP 02 ONLINE",
            },
            "sinter sp 02 basicity": {
                "table": "sinter_chemistry",
                "db_material": "SINTER SP 02 ONLINE",
            },
            # Ore 1-12
            "ore 1": {"table": "ore_chemistry", "db_material": "ORE 1"},
            "ore 2": {"table": "ore_chemistry", "db_material": "ORE 2"},
            "ore 3": {"table": "ore_chemistry", "db_material": "ORE 3"},
            "ore 4": {"table": "ore_chemistry", "db_material": "ORE 4"},
            "ore 5": {"table": "ore_chemistry", "db_material": "ORE 5"},
            "ore 6": {"table": "ore_chemistry", "db_material": "ORE 6"},
            "ore 7": {"table": "ore_chemistry", "db_material": "ORE 7"},
            "ore 8": {"table": "ore_chemistry", "db_material": "ORE 8"},
            "ore 9": {"table": "ore_chemistry", "db_material": "ORE 9"},
            "ore 10": {"table": "ore_chemistry", "db_material": "ORE 10"},
            # Pellet
            "ore pellet 1": {"table": "ore_chemistry", "db_material": "PELLET 1"},
        }

    def _parse_column(self, col_name: str) -> tuple[str, str] | None:
        """
        Parse column name to extract category-material key and property.
        
        Examples:
        - "fuel coke online ash" -> ("fuel coke online", "ash")
        - "flux 1 cao" -> ("flux 1", "cao")
        - "ore 1 al2o3" -> ("ore 1", "al2o3")
        """
        col_lower = col_name.lower().strip()
        
        # Try to match against known patterns
        for pattern in self.category_map.keys():
            if col_lower.startswith(pattern + " "):
                property = col_lower[len(pattern) + 1:].strip()
                return (pattern, property)
        
        return None

    def _get_material_id(self, material_name: str) -> int | None:
        """Get material ID from lookup"""
        return self.material_lookup.get(material_name.upper())

    def iter_table_dfs(self, combined_df: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame]]:
        """
        Convert combined dataframe to per-table dataframes for Neon DB.
        
        Yields: (table_name, dataframe)
        """
        # Get all columns except 'date'
        data_cols = [c for c in combined_df.columns if c != "date"]
        
        # Group columns by table
        table_groups = {}
        
        for col in data_cols:
            parsed = self._parse_column(col)
            if parsed:
                pattern, property = parsed
                mapping = self.category_map.get(pattern)
                if mapping:
                    table_name = mapping["table"]
                    db_material = mapping["db_material"]
                    
                    if table_name not in table_groups:
                        table_groups[table_name] = {}
                    if db_material not in table_groups[table_name]:
                        table_groups[table_name][db_material] = []
                    table_groups[table_name][db_material].append((col, property))
        
        # For each table, create separate dataframes per material
        for table_name, materials in table_groups.items():
            for db_material, cols_props in materials.items():
                # Get material_id from lookup
                material_id = self._get_material_id(db_material)
                if material_id is None:
                    if self.logger:
                        self.logger.warning(f"Material not found in DB: {db_material}")
                    continue
                
                # Build dataframe for this material
                col_names = [c for c, p in cols_props]
                df_material = combined_df[["date"] + col_names].copy()
                
                # Rename columns to just property names
                rename_cols = {c: p for c, p in cols_props}
                df_material = df_material.rename(columns=rename_cols)
                
                # Add material_id and rename date to date_time
                df_material["material_id"] = material_id
                df_material = df_material.rename(columns={"date": "date_time"})
                
                # Reorder columns: date_time, properties..., material_id
                prop_cols = [c for c in df_material.columns if c not in ["date_time", "material_id"]]
                df_material = df_material[["date_time"] + prop_cols + ["material_id"]]
                
                yield (table_name, df_material)