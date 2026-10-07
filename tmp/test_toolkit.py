try:
    from financetoolkit import Toolkit
    import os

    print("Testing FinanceToolkit with empty key...")
    # Using a common ticker
    toolkit = Toolkit(
        tickers=['AAPL'],
        api_key="",
        use_cached_data=False
    )
    
    print("Attempting to fetch ratios...")
    ratios = toolkit.ratios.collect_valuation_ratios()
    print("Success!")
    print(ratios.head())
except Exception as e:
    print(f"Caught expected error: {e}")
