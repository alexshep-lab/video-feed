from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("backend.main:app", host="127.0.0.1", port=7999, reload=False)


if __name__ == "__main__":
    main()
