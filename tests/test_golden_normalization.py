from dronautix_uploader.core.golden_normalization import canonical_cloudjs_text, canonical_json_text


def test_canonical_json_masks_volatile_fields_and_sorts_keys():
    raw = """
    {
      "projekt": "München",
      "datum": "2026-06-21T12:00:00",
      "id": "abcdef12",
      "nested": {"b": 2, "a": 1.123456789}
    }
    """

    assert canonical_json_text(raw) == (
        '{\n'
        '  "datum": "<volatile>",\n'
        '  "id": "<id>",\n'
        '  "nested": {\n'
        '    "a": 1.12345679,\n'
        '    "b": 2\n'
        '  },\n'
        '  "projekt": "München"\n'
        '}\n'
    )


def test_canonical_cloudjs_strips_assignment_wrapper():
    raw = 'cloud.js = {"projection":"EPSG:25832","last_updated":"now"};'

    assert canonical_cloudjs_text(raw) == (
        '{\n'
        '  "last_updated": "<volatile>",\n'
        '  "projection": "EPSG:25832"\n'
        '}\n'
    )
