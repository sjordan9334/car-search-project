from car_watcher import extract_listings


def test_extracts_json_ld_vehicle():
    html = """
    <html><head>
    <script type='application/ld+json'>
      {"@type":"Vehicle","name":"2018 Honda CR-V","offers":{"price":"23995"},"url":"/listing/123","sku":"abc-123","description":"clean title single owner"}
    </script>
    </head></html>
    """

    listings = extract_listings(html, "https://www.autotempest.com/results", "CR-V")
    assert len(listings) == 1
    assert listings[0]["id"] == "abc-123"
    assert listings[0]["price"] == 23995
    assert listings[0]["url"].startswith("https://")
