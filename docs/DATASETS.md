# Public dataset catalog

The `benchforge datasets list` command reports source metadata and current local
file/summary presence without network access. `benchforge datasets fetch` and
`benchforge datasets validate` support the five implemented processors only;
they do not certify current license terms.
Availability and current publisher terms were reviewed on 2026-10-30. A
publisher page is not the same as a validated download endpoint or a completed
integration. Household Power and Wine Quality were downloaded from official
UCI endpoints, processed, and recorded with source hashes. UCI currently lists
CC BY 4.0 for each of the five UCI datasets below; its pages permit sharing and
adaptation for any purpose with appropriate credit. The linked dataset pages
and citations are the attribution sources.

| Dataset | Publisher / official source | Current integration and local artifact | Terms / attribution |
|---|---|---|---|
| UCI Online Retail | [UCI dataset 352](https://archive.ics.uci.edu/dataset/352/online+retail) | Implemented XLSX-in-ZIP processor; local archive 23,715,478 bytes; stored aggregate reports 541,909 source rows (historical run). Full source file, not a row sample. | CC BY 4.0. Cite Chen (2015), *Online Retail*, DOI [10.24432/C5BW33](https://doi.org/10.24432/C5BW33), and UCI. Sharing/adaptation, including commercial use, is permitted with attribution under the stated license. |
| UCI Bike Sharing | [UCI dataset 275](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset) | Implemented hourly CSV-in-ZIP processor; local archive 279,992 bytes; processed hourly table count was 17,379 in previous processing. | CC BY 4.0. Cite Fanaee-T (2013), *Bike Sharing*, DOI [10.24432/C5W894](https://doi.org/10.24432/C5W894), and UCI. The UCI page reports 17,389 instances; the historical local hourly count should be revalidated before release. |
| NYC Yellow Taxi, 2024-01 | [NYC TLC trip record data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) and matching [NYC Open Data 2024 Yellow Taxi Trip Data record](https://data.cityofnewyork.us/Transportation/2024-Yellow-Taxi-Trip-Data/rwwi-khbc) | Implemented PyArrow Parquet processor; local file `yellow_tripdata_2024-01.parquet`, 49,961,641 bytes; processed first 100,000 rows in file order. This is a sample, not the full month. Raw input and summaries are outside the Python package. | The official TLC page provides monthly Parquet downloads. The matching NYC Open Data record lists its license as **unspecified**. NYC Open Data's [FAQ](https://www.nyc.gov/opendata/get-started/FAQs) states there are no restrictions on use of Open Data, but no TLC archive-specific redistribution notice was found. Local analysis is supported by the public-data guidance; raw redistribution, bundling in a wheel/sdist, commercial redistribution, and distribution of derived summaries remain **BLOCKED pending TLC clarification**. Attribute NYC TLC and the data record if used. TLC states source data was submitted by technology providers and disclaims accuracy. |
| Citi Bike Trip Histories | [Citi Bike system data](https://citibikenyc.com/system-data) | Not implemented. The official S3 listing exposes `202401-citibike-tripdata.zip` at 369,035,302 bytes. No archive was downloaded; it exceeds current per-source download limits. | [Citi Bike Data Sharing Policy](https://citibikenyc.com/data-sharing-policy) grants use for lawful purposes but prohibits hosting, distributing, or selling the data as a stand-alone dataset. It allows source material in analyses/reports/studies distributed for non-commercial purposes; it also restricts scraping, attempts to re-identify subscribers, and unapproved marks/endorsement. Obtain legal review before any commercial derived-data distribution; no file is included. |
| UCI Student Performance | [UCI dataset 320](https://archive.ics.uci.edu/dataset/320/student-performance) | Not implemented. UCI describes Math and Portuguese CSV files; its API reports 649 instances for the current dataset record. No archive was downloaded. | CC BY 4.0. Cite Cortez (2008), *Student Performance*, DOI [10.24432/C5TG7T](https://doi.org/10.24432/C5TG7T), and UCI. Attribution is required. The source contains student demographic, social, and school-related attributes; minimize exposure and assess privacy even though the page licenses the dataset. |
| UCI Individual Household Electric Power Consumption | [UCI dataset 235](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption) | Implemented and processed locally. The ZIP is 20,640,916 bytes; its text file is 132,960,755 bytes. All 2,075,259 readings were processed; 25,979 rows have one or more missing measurements. SHA-256: `9f84b46ade8a2d8e1286ec4b2b6c2987a45a755c59f263be3b3b3d10dfbda3ff`. | CC BY 4.0. Cite Hebrail and Berard (2006), *Individual Household Electric Power Consumption*, DOI [10.24432/C58K54](https://doi.org/10.24432/C58K54), and UCI. Attribute the source; avoid exposing granular household time series. |
| UCI Wine Quality | [UCI dataset 186](https://archive.ics.uci.edu/dataset/186/wine+quality) | Implemented and processed locally. The ZIP is 91,353 bytes; 6,497 records were read (1,599 red and 4,898 white). The processed count sums both CSV files; UCI's API reports 4,898 instances. SHA-256: `3ed56667f4b828242bd732d7d1dd7f2861e54432239d7fa63877014cbb0304d4`. | CC BY 4.0. Cite Cortez et al. (2009), *Wine Quality*, DOI [10.24432/C56S3T](https://doi.org/10.24432/C56S3T), and UCI. |

## Replacement for the unavailable survey source

NYC Open Data's exact survey resource could not be verified (the portal request
returned HTTP 403). UCI Wine Quality is used as the seventh catalog source
instead. The current seven-entry catalog has five processed integrations and
two metadata-only entries (Citi Bike and Student Performance); neither blocked
source is counted as a validated integration.

## Reproducible local data already present

Raw public source files are stored under the ignored
`data/public_datasets/raw/` directory, with compact outputs in the ignored
`data/public_datasets/processed/` directory. Five source archives and
summaries are present. Do not copy these into a public static demo without
reviewing each dataset's rights. Reprocess supported sources using
`benchforge datasets validate DATASET --source PATH`, or fetch and validate
using `benchforge datasets validate DATASET --download`. Bike, retail,
household, and wine inputs stream records; taxi processing takes a bounded
first-N file-order sample by default. Each summary records the source filename,
byte size, and SHA-256 digest.

The catalog deliberately reports unimplemented sources instead of treating
official URLs as successful integration. The 2026-10-30 review confirms UCI's
current CC BY 4.0 statements and attributes those sources above. The TLC-linked
NYC Open Data record for the 2024 Yellow Taxi dataset has an unspecified license
field. The City's general FAQ says use of NYC Open Data is unrestricted, but
TLC-specific terms for redistributing this direct-download archive, bundling it
in a wheel/sdist, or distributing derived summaries were not explicit. Keep TLC
raw data and summaries out of public package artifacts until TLC confirms those
uses. The wheel inspected during the release closure contains no `data/` paths
or Parquet files. Citi Bike's policy expressly forbids standalone dataset
redistribution. Do not package or publish raw sources unless their terms permit
that use. Before adding any dataset, verify its official endpoint, file format
and size, terms, attribution, schema, sampling policy, and a fixture-backed
transformation.

## Official terms checked on 2026-10-30

- [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail), [Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset), [Household Power](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption), [Wine Quality](https://archive.ics.uci.edu/dataset/186/wine+quality), and [Student Performance](https://archive.ics.uci.edu/dataset/320/student-performance) each state CC BY 4.0 and give a citation. Cite the paper/dataset record and UCI.
- [Citi Bike Data Sharing Policy](https://citibikenyc.com/data-sharing-policy) is the applicable specific agreement for its system data. Do not distribute it standalone; do not infer permission for commercial redistribution of derived datasets from the limited license language.
- [NYC TLC's trip-record page](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) supplies the downloads and data caveats but no dataset-specific license text. The City's [Open Data policy](https://a860-gpp.nyc.gov/downloads/5712m8144?locale=en) describes a broad public-data default with exceptions; the general [NYC.gov terms](https://www.nyc.gov/main/terms-of-use) reserve rights in website materials. For this TLC-hosted archive, obtain TLC clarification before redistributing raw files or derived data.
- The matching [NYC Open Data 2024 Yellow Taxi Trip Data record](https://data.cityofnewyork.us/Transportation/2024-Yellow-Taxi-Trip-Data/rwwi-khbc) lists the license as unspecified. The official [NYC Open Data FAQ](https://www.nyc.gov/opendata/get-started/FAQs) says there are no restrictions on use, but it does not resolve this TLC-hosted archive's package-bundling and redistribution terms. Treat raw and derived public distribution as blocked until TLC confirms.
