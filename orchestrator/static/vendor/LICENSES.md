Third-party files vendored unchanged for the Knowledge Graph management pages
(LUM-941). They are not under the project licence (AGPL-3.0-only); each keeps its
own licence below.

## cytoscape 3.28.1

- Vendored file: `cytoscape-3.28.1.min.js`
- Source: npm tarball `cytoscape-3.28.1.tgz`, path `package/dist/cytoscape.min.js`
- Registry `dist.integrity`: `sha512-xyItz4O/4zp9/239wCcH8ZcFuuZooEeF8KHRmzjDfGdXsj3OG9MFSMA0pJE0uX3uCN/ygof6hHf4L7lst+JaDg==`
- SHA-256: `92d752b48ea949720675865197fd2a0001c95bc5888545e990af60321712d4c6`
- SRI: `sha384-J7Q85oZE4GJ/e7+n2aOQsLXfDwwfnA8S2nZAL5BpFsfpCF84zQD7LroZ/dMnLgex`
- Licence text source: the tarball's `package/LICENSE` (MIT)

```text
Copyright (c) 2016-2023, The Cytoscape Consortium.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## alpinejs 3.14.8

- Vendored file: `alpine-3.14.8.min.js`
- Source: npm tarball `alpinejs-3.14.8.tgz`, path `package/dist/cdn.min.js`
- Registry `dist.integrity`: `sha512-wT2fuP2DXpGk/jKaglwy7S/IJpm1FD+b7U6zUrhwErjoq5h27S4dxkJEXVvhbdwyPv9U+3OkUuNLkZT4h2Kfrg==`
- SHA-256: `b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643`
- SRI: `sha384-X9kJyAubVxnP0hcA+AMMs21U445qsnqhnUF8EBlEpP3a42Kh/JwWjlv2ZcvGfphb`
- Licence text source: the tarball has no licence file (`package.json` says
  `"license": "MIT"`), so the text is `LICENSE.md` from the upstream tag `v3.14.8`,
  `https://raw.githubusercontent.com/alpinejs/alpine/v3.14.8/LICENSE.md`, copied verbatim

```text
# MIT License

Copyright © 2019-2021 Caleb Porzio and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Pinned versions. To bump: replace both copies (orchestrator/static/vendor and
services/lumogis-graph/static/vendor), update the SHA-256 pins in
orchestrator/tests/test_graph_page_assets.py, the `integrity` attributes in both
graph_mgm.html copies and graph_viz.html, the allowlist in both route modules, and
this file, in one commit. A new version always gets a new file name: never
re-vendor different bytes under an existing name, because the route serves these
files with a one-year immutable cache. The vendor folders are marked `-text` in
.gitattributes so line endings are never converted; keep that when adding a file.
Newer releases at plan time: cytoscape 3.34.x, Alpine 3.15.x.
