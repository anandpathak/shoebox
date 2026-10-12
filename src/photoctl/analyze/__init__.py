"""Image analysis on the Mac: what's in a photo (scene labels) and who (faces), so it can be searched.

* Labels come from Apple's Vision framework (on-device, the engine Photos uses) and become ordinary tags
  with source "vision": they sync to every device and can be removed like any tag.
* Faces are found and fingerprinted with OpenCV (YuNet + SFace, small open models downloaded once).
  Fingerprints are biometric data, so they stay in ~/.photoctl/faces.sqlite on this Mac: never synced,
  exported or backed up. Only names the user confirms become people on photos (and those sync).
"""

VERSION = 2   # bump to re-analyse everything after a change in what's extracted (2: text in the picture)
