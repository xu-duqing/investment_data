try:
    from .build_temporal_delta import main
except ImportError:
    from build_temporal_delta import main


if __name__ == "__main__":
    main()
