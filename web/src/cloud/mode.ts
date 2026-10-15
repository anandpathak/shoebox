// Viewer mode: the app reads the library straight from S3 (read-only), with no Mac. Used by the copy of the
// web app bundled in the phone app (…/browse/) and, for testing, any page opened with ?viewer=1.
export function isViewerMode(loc: { pathname: string; search: string } = location): boolean {
  return /\/browse\//.test(loc.pathname) || new URLSearchParams(loc.search).has('viewer')
}
