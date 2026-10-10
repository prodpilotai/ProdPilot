# Third-party notices

ProdPilot ships these components inside its package, under
`src/prodpilot/vendor/node_modules/`. Each keeps its own license file beside
its code.

| Component | Version | License | Copyright |
| --- | --- | --- | --- |
| acorn, a JavaScript parser | 8.18.0 | MIT | Copyright (C) 2012-2022 by various contributors |
| acorn-jsx, JSX support for acorn | 5.3.2 | MIT | Copyright (C) 2012-2017 by Ingvar Stepanyan |

ProdPilot's own dependencies, such as the MCP SDK, scikit-learn and the Docker
SDK, are installed from PyPI under their own licenses and are not shipped
inside this package.

The test suite, which is not part of the package, carries one more file:
`tests/fixtures/sarif/sarif-schema-2.1.0.json`, the official SARIF 2.1.0 JSON
schema from OASIS, used to validate ProdPilot's SARIF output. Its notice and
source are in `tests/fixtures/sarif/LICENSE.txt`.
