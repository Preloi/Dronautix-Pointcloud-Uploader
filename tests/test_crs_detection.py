import struct

import pytest

from dronautix_uploader.core.crs_detection import detect_las_crs, extract_epsg_from_wkt


MGI_GK_EAST_WKT1 = (
    'PROJCS["MGI / Austria GK East",GEOGCS["MGI",DATUM["Militar_Geographische_Institute",'
    'SPHEROID["Bessel 1841",6377397.155,299.1528128,AUTHORITY["EPSG","7004"]],AUTHORITY["EPSG","6312"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4312"]],PROJECTION["Transverse_Mercator"],PARAMETER["central_meridian",16.3333333333333],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]]{authority}]'
)


def test_wkt1_projcs_with_own_authority_returns_crs_code():
    wkt = MGI_GK_EAST_WKT1.format(authority=',AUTHORITY["EPSG","31256"]')

    assert extract_epsg_from_wkt(wkt) == "31256"


def test_wkt1_projcs_without_own_authority_never_returns_unit_or_base_crs_code():
    wkt = MGI_GK_EAST_WKT1.format(authority="")

    assert extract_epsg_from_wkt(wkt) == ""


def test_wkt1_geogcs_without_own_authority_never_returns_angle_unit_code():
    wkt = (
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]]'
    )

    assert extract_epsg_from_wkt(wkt) == ""


def test_wkt2_projcrs_id_with_unquoted_code_is_detected():
    wkt = (
        'PROJCRS["ETRS89 / UTM zone 33N",BASEGEOGCRS["ETRS89",DATUM["European Terrestrial Reference System 1989",'
        'ELLIPSOID["GRS 1980",6378137,298.257222101,LENGTHUNIT["metre",1]]],ID["EPSG",4258]],'
        'CONVERSION["UTM zone 33N",METHOD["Transverse Mercator",ID["EPSG",9807]]],'
        'CS[Cartesian,2],AXIS["(E)",east],LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",25833]]'
    )

    assert extract_epsg_from_wkt(wkt) == "25833"


def test_compound_wkt1_detects_horizontal_code_separately_from_vertical():
    wkt = (
        'COMPD_CS["MGI GK East + GHA",'
        + MGI_GK_EAST_WKT1.format(authority=',AUTHORITY["EPSG","31256"]')
        + ',VERT_CS["GHA height",VERT_DATUM["Gebrauchshoehen ADRIA",2005],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
        'AUTHORITY["EPSG","5778"]]]'
    )

    assert extract_epsg_from_wkt(wkt) == "31256"


def _las_with_vlrs(tmp_path, vlrs, name="cloud.las"):
    header_size = 227
    vlr_bytes = b""
    for record_id, data in vlrs:
        vlr_bytes += struct.pack("<H16sHH32s", 0, b"LASF_Projection", record_id, len(data), b"")
        vlr_bytes += data
    header = bytearray(header_size)
    header[0:4] = b"LASF"
    header[24] = 1
    header[25] = 2
    struct.pack_into("<H", header, 94, header_size)
    struct.pack_into("<I", header, 96, header_size + len(vlr_bytes))
    struct.pack_into("<I", header, 100, len(vlrs))
    path = tmp_path / name
    path.write_bytes(bytes(header) + vlr_bytes)
    return path


def _geo_key_directory(*entries):
    values = [1, 1, 0, len(entries)]
    for entry in entries:
        values.extend(entry)
    return struct.pack("<" + "H" * len(values), *values)


def test_las_math_transform_wkt_record_is_not_used_as_crs(tmp_path):
    path = _las_with_vlrs(
        tmp_path,
        [
            (2112, MGI_GK_EAST_WKT1.format(authority=',AUTHORITY["EPSG","31256"]').encode() + b"\0"),
            (2111, b'PARAM_MT["Affine",PARAMETER["elt_0_0",1],AUTHORITY["EPSG","9624"]]\0'),
        ],
    )

    crs_info = detect_las_crs(str(path))

    assert crs_info is not None
    assert crs_info["value"] == "EPSG:31256"


def test_las_wkt_without_usable_crs_falls_back_to_geotiff_keys(tmp_path):
    path = _las_with_vlrs(
        tmp_path,
        [
            (2112, b"LOCAL_CS[]\0"),
            (34735, _geo_key_directory((3072, 0, 1, 25833))),
        ],
    )

    crs_info = detect_las_crs(str(path))

    assert crs_info is not None
    assert crs_info["value"] == "EPSG:25833"


@pytest.mark.parametrize("authority", ["", ',AUTHORITY["EPSG","31256"]'])
def test_las_wkt_detection_never_reports_unit_code(tmp_path, authority):
    path = _las_with_vlrs(tmp_path, [(2112, MGI_GK_EAST_WKT1.format(authority=authority).encode() + b"\0")])

    crs_info = detect_las_crs(str(path))

    assert crs_info is None or "9001" not in str(crs_info.get("value", ""))
