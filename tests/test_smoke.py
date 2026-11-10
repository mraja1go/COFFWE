from coffwe import main


def test_main_runs(capsys):
    main()
    captured = capsys.readouterr()
    assert "coffwe" in captured.out.lower()
