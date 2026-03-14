"""Entrypoint — suppress C-level warnings before any imports."""

import warnings

# Must run before PyMuPDF/SWIG loads — C extensions emit DeprecationWarning
# from <sys>:0 which can only be caught if the filter is set first.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, message="Pydantic serializer warnings")


def main():
    from blueclaw.cli import app
    app()


if __name__ == "__main__":
    main()
