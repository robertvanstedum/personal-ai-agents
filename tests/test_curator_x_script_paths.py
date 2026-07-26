from pathlib import Path

from scripts.x import paths, x_to_article


ROOT = Path(__file__).resolve().parents[1]


def test_x_scripts_keep_existing_data_and_state_locations():
    assert paths.PROJECT_ROOT == ROOT
    assert paths.SIGNALS_FILE == ROOT / "data" / "curator" / "curator_signals.json"
    assert paths.STATE_FILE == ROOT / "x_pull_state.json"
    assert x_to_article.PROJECT_DIR == ROOT
    assert x_to_article.SIGNALS_FILE == paths.SIGNALS_FILE


def test_x_article_loader_runs_from_its_package_location():
    articles = x_to_article.load_x_bookmark_articles()

    assert isinstance(articles, list)
    assert articles


def test_curator_cron_callers_use_the_packaged_runner():
    telegram_source = (ROOT / "core" / "telegram" / "telegram_bot.py").read_text()
    local_cron_source = (
        ROOT / "scripts" / "operations" / "run_curator_cron.sh"
    ).read_text()
    ec2_cron_source = (ROOT / "scripts" / "run_curator_cron_ec2.sh").read_text()

    assert 'CURATOR_CRON_SCRIPT = PROJECT_ROOT / "scripts" / "operations"' in telegram_source
    assert "BASE_DIR / 'run_curator_cron.sh'" not in telegram_source
    assert "python -m scripts.x.x_pull_incremental" in local_cron_source
    assert "python -m scripts.x.x_pull_incremental" in ec2_cron_source
