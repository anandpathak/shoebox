// SQL shared with the Python side: the search queries and the catalog migrations.
import devicesSql from '../../../queries/devices.sql?raw'
import detailSql from '../../../queries/media_detail.sql?raw'
import peopleSql from '../../../queries/people.sql?raw'
import searchSql from '../../../queries/search.sql?raw'
import m1 from '../../../src/photoctl/catalog/migrations/0001_initial.sql?raw'
import m2 from '../../../src/photoctl/catalog/migrations/0002_deferred_ops.sql?raw'
import m3 from '../../../src/photoctl/catalog/migrations/0003_restore_requests.sql?raw'

export const QUERIES = { search: searchSql, media_detail: detailSql, people: peopleSql, devices: devicesSql }
export const MIGRATIONS: [number, string][] = [[1, m1], [2, m2], [3, m3]]
