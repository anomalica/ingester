OLD_BODY = """Photo by Heidi Kaden on Unsplash

Written by Christopher Sharp - 24 April 2026

**Burlison has said** he has

that the death appears

“grave concerns”, suggesting the officer may have been silenced.

<!--
image:
  file: 5f2f548aea81.jpg
  caption: "A photo"
-->

<!-- irrelevant: start -->

Love our content and wish to support the website?

You can now become a Patron: Liberation Times | Patreon

<!-- irrelevant: end -->
"""

FRESH_BODY = """Photo by [Heidi Kaden](https://unsplash.com/x) on Unsplash

Written by [Christopher Sharp](https://twitter.com/x) - 24 April 2026

Burlison has said he has “grave concerns” that the death appears “suspicious”, suggesting the officer may have been silenced.

<!--
image:
  caption: "A photo"
-->
"""

FRONTMATTER = """schema: anomalica/record/1
title: "Late Officer"
date_published: 2026-04-24
source_type: web
file_format: html
source_url: https://www.liberationtimes.com/home/late-officer
publisher: "Liberation Times"
content_hash: sha256:{h}
source_hash: sha256:{sh}
date_accessed: 2026-08-13T00:48:13+00:00
date_extracted: 2026-08-13T00:49:01+00:00
copyright:
  status: publicly_accessible
processing:
  handler: webpage
  version: d4f18fe
  pipeline_version: 2
  tools:
    - name: trafilatura
      version: "2.1.0"
      role: extraction
      provider: local
quality:
  replacement_chars: 0
  substitution_score: 0.0"""
