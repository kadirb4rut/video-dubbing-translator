from __future__ import annotations

from app.db import _aurora_resume_error


class ProviderError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def test_aurora_resume_error_matches_only_transient_provider_codes() -> None:
    assert _aurora_resume_error(ProviderError("DatabaseResumingException"))
    assert _aurora_resume_error(ProviderError("HttpEndpointNotEnabledException"))
    assert not _aurora_resume_error(ProviderError("BadRequestException"))


def test_aurora_resume_error_checks_wrapped_exceptions() -> None:
    cause = ProviderError("DatabaseResumingException")
    wrapped = RuntimeError("sqlalchemy wrapper")
    try:
        raise cause
    except ProviderError as error:
        try:
            raise wrapped from error
        except RuntimeError as error:
            assert _aurora_resume_error(error)
