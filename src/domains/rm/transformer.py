# src/domains/rm/transformer.py

import pandas as pd
from datetime import datetime
from typing import List


class RMTransformer:
    VALID_SHIFTS = {"A", "B", "C"}
    # Values that indicate missing/invalid data - should be treated as NULL
    INVALID_MARKERS = {"STOP", "stop", "Stop", "N/A", "NA", "n/a", "na", "-", ""}

    def __init__(self, logger):
        self.logger = logger

    # ----------------------------
    # helpers (ported as-is)
    # ----------------------------
    def normalize_columns(self, df):
        df = df.copy()
        df.columns = df.columns.str.strip().str.replace(" ", "_").str.upper()
        return df

    def filter_invalid_markers(self, df):
        """
        Remove rows containing invalid markers like 'STOP' in any column.
        These markers indicate missing analysis data and should not be
        inserted into the database as they cause type errors.
        """
        df = df.copy()
        mask = pd.Series([False] * len(df), index=df.index)

        for col in df.columns:
            if df[col].dtype == object:
                # Check for invalid markers in string columns
                mask |= df[col].astype(str).str.strip().isin(self.INVALID_MARKERS)

        # Also check for 'STOP' in any column (case-insensitive search)
        for col in df.columns:
            col_str = df[col].astype(str).str.strip().str.upper()
            mask |= col_str.eq("STOP")

        valid_rows = ~mask
        if mask.sum() > 0:
            self.logger.info(f"  Filtered {mask.sum()} rows with invalid markers (STOP, etc.)")

        return df[valid_rows]

    def average_numeric_group(self, df, group_cols, skip_cols=None, preserve_order_col="MERGE_KEY"):
        skip_cols = skip_cols or []
        results = []

        order_map = (
            df.drop_duplicates(preserve_order_col)
            .reset_index()
            .set_index(preserve_order_col)["index"]
            .to_dict()
        )

        for keys, group in df.groupby(group_cols):
            group = group.drop(columns=skip_cols, errors="ignore")
            avg_row = {}

            for col in group.columns:
                if col in group_cols:
                    continue

                converted = pd.to_numeric(group[col], errors="coerce")
                valid_ratio = converted.notna().sum() / len(group)

                if valid_ratio >= 0.5:
                    cleaned = converted.mask(converted == 0, pd.NA)
                    avg_val = cleaned.mean(skipna=True)
                    if pd.notna(avg_val):
                        avg_row[col] = avg_val

            if isinstance(keys, tuple):
                for i, col in enumerate(group_cols):
                    avg_row[col] = keys[i]
                merge_key = f"{keys[0]}_{keys[1]}"
            else:
                avg_row[group_cols[0]] = keys
                merge_key = str(keys)

            avg_row["_ORDER"] = order_map.get(merge_key, -1)
            avg_row[preserve_order_col] = merge_key
            results.append(avg_row)

        if not results:
            return pd.DataFrame()

        df_out = pd.DataFrame(results)
        df_out.sort_values("_ORDER", inplace=True)
        df_out.drop(columns=["_ORDER"], inplace=True)
        return df_out

    def average_shift_blocks(self, df):
        df = df.copy()
        df["SHIFT_GROUP"] = df["SHIFT"].str[0].str.upper()
        df["MERGE_KEY"] = df["DATE"].astype(str) + "_" + df["SHIFT_GROUP"]

        shift_order = df.drop_duplicates("MERGE_KEY")["MERGE_KEY"].tolist()

        skip_cols = [c for c in df.columns if c.upper() in {"SHIFT", "SHIFT_GROUP"}]

        avg_df = self.average_numeric_group(
            df,
            group_cols=["DATE", "SHIFT_GROUP"],
            skip_cols=skip_cols,
            preserve_order_col="MERGE_KEY"
        )

        if avg_df.empty:
            return pd.DataFrame(columns=["DATE", "SHIFT"])

        avg_df.rename(columns={"SHIFT_GROUP": "SHIFT"}, inplace=True)

        avg_df["MERGE_KEY"] = avg_df["DATE"].astype(str) + "_" + avg_df["SHIFT"]
        avg_df["_ORDER"] = avg_df["MERGE_KEY"].apply(
            lambda x: shift_order.index(x) if x in shift_order else -1
        )

        avg_df = (
            avg_df
            .sort_values("_ORDER")
            .drop(columns=["MERGE_KEY", "_ORDER"])
            .reset_index(drop=True)
        )

        cols = ["DATE", "SHIFT"] + [c for c in avg_df.columns if c not in {"DATE", "SHIFT"}]
        avg_df = avg_df[cols]

        self.logger.info(" AVG is Done")
        return avg_df

    # ----------------------------
    # filters
    # ----------------------------
    def filter_by_date_and_shift(self, df, date_list, sheet_name):
        df = df.copy()
        df["DATE"] = pd.to_datetime(
            df["DATE"],
            format="%d-%m-%Y",
            errors="coerce"
        ).dt.date


        if sheet_name == "SP-02 (RI-RDI)":
            return df[df["DATE"].isin(date_list)]

        if "SHIFT" not in df.columns:
            return pd.DataFrame()

        df["SHIFT"] = df["SHIFT"].astype(str).str.strip().str.upper()
        df["SHIFT"] = df["SHIFT"].apply(
            lambda x: x[0] if x and x[0] in self.VALID_SHIFTS else pd.NA
        )

        return df[df["DATE"].isin(date_list) & df["SHIFT"].notna()]

    def split_online_offline_and_merge(self, df):
        df = self.normalize_columns(df)
        df["ONLINE/OFFLINE"] = df["ONLINE/OFFLINE"].astype(str).str.strip()
        df["SHIFT"] = df["SHIFT"].astype(str).str.strip()
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce", dayfirst=True).dt.date

        if "BNK_NO" in df.columns:
            df["BNK_NO"] = df["BNK_NO"].astype(str).str.strip()

        df["MERGE_KEY"] = df["DATE"].astype(str) + "_" + df["SHIFT"]
        df["IS_ONLINE"] = df["ONLINE/OFFLINE"].str.contains(r"(?:ONLINE|EML)", case=False, na=False)

        def reduce(g, suffix):
            base = {c: g[c].iloc[0] for c in ["MERGE_KEY", "DATE", "SHIFT"] if c in g.columns}
            numeric_cols = []
            num_data = {}

            for col in g.columns:
                if col in base or col in {"IS_ONLINE", "MERGE_KEY", "DATE", "SHIFT"}:
                    continue

                converted = pd.to_numeric(g[col], errors="coerce")
                if converted.notna().sum() >= 0.8 * len(g):
                    vals = converted.replace(0, pd.NA).dropna()
                    num_data[col + suffix] = round(vals.mean(skipna=True), 3) if not vals.empty else pd.NA
                    numeric_cols.append(col)

            for col in g.columns:
                if col in base or col in numeric_cols or col == "IS_ONLINE":
                    continue
                unique_vals = g[col].dropna().astype(str).str.strip().unique()
                base[col + suffix] = " | ".join(sorted(set(unique_vals)))

            base.update(num_data)
            return base

        records = []
        for (_, shift), group in df.groupby(["MERGE_KEY", "SHIFT"]):
            online, offline = group[group["IS_ONLINE"]], group[~group["IS_ONLINE"]]
            combined = {}
            if not online.empty:
                combined.update(reduce(online, "_ON"))
            if not offline.empty:
                combined.update(reduce(offline, "_OFF"))
            records.append(combined)

        out = pd.DataFrame(records)
        final = pd.merge(df.drop_duplicates("MERGE_KEY")[["MERGE_KEY"]], out, on="MERGE_KEY")
        return final.drop(columns=["MERGE_KEY"])
