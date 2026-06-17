import pytest
import geopandas as gpd
from shapely.geometry import Polygon, Point
from pathlib import Path
from scf_toolkit import(
    resolve_secure_path,
    extract_counties,
    filter_counties_msa,
    sjoin_gdfs_in_3081,
    extract_bounded_zctas,
    dissolve_by_scf,
    output_4326_geojson
)


def test_resolve_secure_path_valid():
    """Checks whether the path resolver finds an existing file."""
    current_script = Path(__file__).name
    resolved_path = resolve_secure_path(current_script)

    assert isinstance(resolved_path, Path)
    assert resolved_path.exists()


def test_resolve_secure_path_missing():
    """Checks whether the system hard-exits when a file is not in the target directory."""
    with pytest.raises(SystemExit) as exit_status:
        resolve_secure_path("fake_ghost_file.shp")

    assert "FileNotFoundError: Core asset missing at absolute location:" in str(exit_status.value)


def test_extract_counties_missing_column(monkeypatch: pytest.MonkeyPatch):
    """Checks whether files missing CNTY_NM column are rejected."""
    monkeypatch.setattr("scf_toolkit.resolve_secure_path", lambda p: Path(p))
    malformed_schema = gpd.GeoDataFrame({'geometry': [Polygon([(0,0), (1, 0), (1,1), (0,1)])]}, crs="EPSG:3081")
    monkeypatch.setattr(gpd, "read_file", lambda *args, **kwargs: malformed_schema)

    with pytest.raises(SystemExit) as exit_status:
        extract_counties("ignored.shp")

    assert "ValidationError: Column 'CNTY_NM' missing from county shapefile attribute table." in str(exit_status.value)


def test_filter_counties_msa_success():
    """Checks whether targeted counties are properly extracted."""
    mock_layer = gpd.GeoDataFrame({'CNTY_NM': ['Dallas', 'Tarrant']}, geometry=[Point(0, 0), Point(1, 1)])
    isolated_gdf = filter_counties_msa(mock_layer, ["  dallas  ", "TARRANT"])

    assert len(isolated_gdf) == 2
    assert "dallas" in isolated_gdf['clean_name'].values
    assert "tarrant" in isolated_gdf['clean_name'].values


def test_filter_counties_msa_empty_exit():
    """Checks whether the system halts if none of the targeted counties exist in the dataset."""
    mock_layer = gpd.GeoDataFrame({'CNTY_NM': ['Travis']}, geometry=[Point(0, 0)])

    with pytest.raises(SystemExit) as exit_status:
        filter_counties_msa(mock_layer, ["Dallas"])

    assert 'ValidationError: None of the provided counties' in str(exit_status.value)


def test_sjoin_gdfs_in_3081():
    """Checks whether the inner point survives spatial join and whether the outer point is discarded."""
    county_boundary = Polygon([(0,0), (1,0), (1,1), (0,1)])
    msa_gdf = gpd.GeoDataFrame({'CNTY_NM': ['Target']}, geometry=[county_boundary], crs="EPSG:3081")

    zcta_gdf = gpd.GeoDataFrame({'ZCTA5CE20': ['75001', '76001']}, geometry=[Point(0.5, 0.5), Point(2.0, 2.0)], crs="EPSG:3081")

    intersection_gdf = sjoin_gdfs_in_3081(msa_gdf, zcta_gdf)
    assert len(intersection_gdf) == 1
    assert intersection_gdf.iloc[0]['ZCTA5CE20'] == '75001'


def test_dissolve_by_scf():
    """Checks whether 5 digit ZIP polygons fuse into a single 3 digit SCF MultiPolygon."""
    zone_a = Polygon([(0,0), (1,0), (1,1), (0,1)])
    zone_b = Polygon([(1,0), (2,0), (2,1), (1,1)])

    sjoined_gdf = gpd.GeoDataFrame({'ZCTA5CE20': ['75001', '75002']}, geometry=[zone_a, zone_b], crs="EPSG:3081")

    scf_gdf = dissolve_by_scf(sjoined_gdf)
    assert len(scf_gdf) == 1
    assert scf_gdf.iloc[0]['SCF_SUBMARKET'] == '750'


def test_output_4326_geojson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Checks whether the output file is the GeoJSON GDF reprojected in EPSG:4326."""
    county_boundary = Polygon([(0,0), (1,0), (1,1), (0,1)])
    gdf = gpd.GeoDataFrame({'SCF_SUBMARKET': ['750']}, geometry=[county_boundary], crs="EPSG:3081")
    monkeypatch.chdir(tmp_path)

    gdf_4326_geojson = output_4326_geojson(gdf, ["Dallas"])
    assert gdf_4326_geojson.crs == "EPSG:4326"

    expected_path = tmp_path / "dallas_scf_submarkets.geojson"
    assert expected_path.exists()

    gdf_reader = gpd.read_file(expected_path)
    assert gdf_reader.crs == "EPSG:4326"
    assert len(gdf_reader) == 1
    assert gdf_reader.iloc[0]['SCF_SUBMARKET'] == '750'


def test_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Checks whether the pipeline runs smoothly inside a temporary sandbox."""
    counties_boundary = Polygon([(0,0), (1,0), (1,1), (0,1)])
    mock_counties_gdf = gpd.GeoDataFrame({'CNTY_NM': ['Dallas']}, geometry=[counties_boundary], crs="EPSG:4269")
    counties_path = tmp_path / "mock_counties.shp"
    mock_counties_gdf.to_file(counties_path)

    mock_zctas_gdf = gpd.GeoDataFrame({'ZCTA5CE20': ['75001', '75002']}, geometry=[Point(0.5, 0.5), Point(0.6, 0.6)], crs="EPSG:4269")
    zctas_path = tmp_path / "mock_zctas_gdf.shp"
    mock_zctas_gdf.to_file(zctas_path)

    monkeypatch.chdir(tmp_path)

    counties_gdf = extract_counties(str(counties_path))
    msa_gdf = filter_counties_msa(counties_gdf, ["Dallas"])
    zcta_gdf = extract_bounded_zctas(str(zctas_path), msa_gdf)

    sjoined_3081_gdf = sjoin_gdfs_in_3081(msa_gdf, zcta_gdf)
    scf_submarkets_gdf = dissolve_by_scf(sjoined_3081_gdf)
    output_4326_geojson(scf_submarkets_gdf, ["Dallas"])

    expected_path = tmp_path / "dallas_scf_submarkets.geojson"
    assert expected_path.exists()

    result_gdf = gpd.read_file(expected_path)
    assert result_gdf.crs == "EPSG:4326"
    assert len(result_gdf) == 1
    assert result_gdf.iloc[0]['SCF_SUBMARKET'] == '750'




