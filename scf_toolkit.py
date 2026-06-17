import geopandas as gpd
import sys
import argparse
from pathlib import Path


def resolve_secure_path(input_path: str) -> Path:
    """Anchors relative input pathways to the runtime scrip directory."""
    resolved = (Path(__file__).parent / Path(input_path)).resolve()

    if not resolved.exists():
        sys.exit(f"FileNotFoundError: Core asset missing at absolute location: '{resolved}'")

    return resolved


def parse_arguments():
    """Exposes runtime settings to command-line arguments."""
    parser = argparse.ArgumentParser(description='SCP (USPS-based) Submarket Toolkit')

    parser.add_argument('-c', '--county', required=True, nargs='+', help='Target county name(s)')
    parser.add_argument('--counties-shp', default='tx-boundaries_48_bnd/counties/txdot_county_detailed_tx.shp', help='County file pathway')
    parser.add_argument('--zcta-shp', default='tl_2025_us_zcta520/tl_2025_us_zcta520.shp', help='ZCTA file pathway')

    return parser.parse_args()


def extract_counties(county_path: str) -> gpd.GeoDataFrame:
    """Ingests and validates the TxDOT county layer."""
    target_path = resolve_secure_path(county_path)
    
    try:
        counties_gdf = gpd.read_file(target_path)
        if 'CNTY_NM' not in counties_gdf.columns:
            sys.exit("ValidationError: Column 'CNTY_NM' missing from county shapefile attribute table.")
    except Exception as e:
        sys.exit(f"EngineError: Failed to parse county vector layer via GeoPandas: {e}")

    return counties_gdf


def filter_counties_msa(counties_gdf: gpd.GeoDataFrame, county_names: list[str]) -> gpd.GeoDataFrame:
    """Filters the targeted counties from the statewide index."""
    target_counties = [name.strip().lower() for name in county_names]
    counties_gdf['clean_name'] = counties_gdf['CNTY_NM'].str.strip().str.lower()
    msa_gdf = counties_gdf[counties_gdf['clean_name'].isin(target_counties)]

    if msa_gdf.empty:
        sys.exit(f'ValidationError: None of the provided counties {county_names} exist in TxDOT database.')

    return msa_gdf


def extract_bounded_zctas(zcta_path: str, msa_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Extracts ZCTAs from federal shapefile, matching bounding box with targeted counties' geometry."""
    target_path = resolve_secure_path(zcta_path)
    
    msa_bbox = msa_gdf.to_crs('EPSG:4269').total_bounds

    try:
        zcta_gdf = gpd.read_file(target_path, bbox=tuple(msa_bbox))
        if zcta_gdf.empty:
            sys.exit('ValidationError: No ZCTAs found within the MSA bounding box. Check ZCTA shapefile coverage or county boundaries.')
    except Exception as e:
        sys.exit(f"EngineError: Failed to parse bounded ZCTA slice: {e}")

    return zcta_gdf


def sjoin_gdfs_in_3081(msa_gdf: gpd.GeoDataFrame, zcta_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Converts both GDFs to EPSG:3081, then spatially joins them into a single GDF."""
    msa_3081 = msa_gdf.to_crs('EPSG:3081')
    zcta_3081 = zcta_gdf.to_crs('EPSG:3081')
    sjoined_3081_gdf = gpd.sjoin(zcta_3081, msa_3081, how='inner', predicate='intersects')

    if sjoined_3081_gdf.empty:
        sys.exit('SpatialJoinError: No ZCTAs intersected with the target county boundaries.')

    return sjoined_3081_gdf


def dissolve_by_scf(sjoined_3081_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Dissolves the spatially joined GDF into submarkets by first 3 digits of ZIP (USPS' SCF)."""
    if not any(col in sjoined_3081_gdf.columns for col in ('ZCTA5CE20', 'GEOID20')):
        sys.exit('ValidationError: No valid ZCTA id column found in schema attributes.')

    zcta_col = 'ZCTA5CE20' if 'ZCTA5CE20' in sjoined_3081_gdf.columns else 'GEOID20'
    sjoined_3081_gdf['SCF_SUBMARKET'] = sjoined_3081_gdf[zcta_col].astype(str).str.strip().str.zfill(5).str[:3]

    scf_submarkets_gdf = sjoined_3081_gdf.dissolve(by='SCF_SUBMARKET')
    scf_submarkets_gdf = scf_submarkets_gdf.reset_index()
    scf_submarkets_gdf = scf_submarkets_gdf[['SCF_SUBMARKET', 'geometry']]

    return scf_submarkets_gdf


def output_4326_geojson(scf_submarkets_gdf: gpd.GeoDataFrame, county_names: list[str]) -> gpd.GeoDataFrame:
    """Normalizes the submarket GDF to web-standard EPSG:4326, then outputs as GeoJSON."""
    scf_4326_gdf = scf_submarkets_gdf.to_crs("EPSG:4326")

    output_counties = '_'.join([name.lower().strip().replace(' ', '_') for name in county_names])
    output_geojson = f'{output_counties}_scf_submarkets.geojson'

    scf_4326_gdf.to_file(output_geojson, driver='GeoJSON')
    print(f'Success: Saved unified submarket layers to {output_geojson}')
    return scf_4326_gdf


def main():
    """Orchestrates all the above functions to transform raw vector data into web-ready submarket layers."""
    args = parse_arguments()

    counties_gdf = extract_counties(args.counties_shp)
    msa_gdf = filter_counties_msa(counties_gdf, args.county)
    zcta_gdf = extract_bounded_zctas(args.zcta_shp, msa_gdf)

    sjoined_3081_gdf = sjoin_gdfs_in_3081(msa_gdf, zcta_gdf)
    scf_submarkets_gdf = dissolve_by_scf(sjoined_3081_gdf)
    output_4326_geojson(scf_submarkets_gdf, args.county)


if __name__ == "__main__":
    main()