class RMStockProcessor:
    def process(self, df, ts):

        # Only add timestamp
        df["time"] = ts

        return df