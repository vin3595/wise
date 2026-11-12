"""Unauthenticated endpoints the login screen needs to show a restaurant's own branding."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..config import get_settings
from ..core.login_id import login_id_from_host, normalize_login_id
from ..db import system_session
from ..models import Brand, Tenant
from ..schemas import BrandOut, PublicBranding

router = APIRouter(prefix="/public", tags=["public"])


def _branding(login_id: str) -> PublicBranding:
    with system_session() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.login_id == normalize_login_id(login_id)))
        if tenant is None or tenant.status == "suspended":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found")
        brands = list(db.scalars(
            select(Brand).where(Brand.tenant_id == tenant.id, Brand.is_active).order_by(Brand.name)))
        return PublicBranding(
            login_id=tenant.login_id, name=tenant.name, app_name=tenant.app_name or tenant.name,
            logo_url=tenant.logo_url, primary_color=tenant.primary_color, ui_theme=tenant.ui_theme,
            show_powered_by=tenant.show_powered_by or tenant.plan != "chain",
            brands=[BrandOut.model_validate(b) for b in brands],
        )


@router.get("/tenants/{login_id}", response_model=PublicBranding)
def branding(login_id: str) -> PublicBranding:
    return _branding(login_id)


@router.get("/resolve-host")
def resolve_host(host: str) -> dict:
    """Map shakewallah.<base_domain> or a custom domain to a login ID."""
    login_id = login_id_from_host(host, get_settings().base_domain)
    if login_id is None:
        with system_session() as db:
            login_id = db.scalar(select(Tenant.login_id).where(Tenant.custom_domain == host.split(":")[0].lower()))
    return {"login_id": login_id}


@router.get("/tenants/{login_id}/manifest.webmanifest")
def manifest(login_id: str) -> JSONResponse:
    """Per-restaurant PWA manifest, so the installed app shows the restaurant's name and logo."""
    b = _branding(login_id)
    # Wise's own PNG icons are always listed, so Chrome offers "Install app" even before a logo is set.
    icons = [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
             {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
             {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    if b.logo_url:
        icons = [{"src": b.logo_url, "sizes": "192x192", "purpose": "any"},
                 {"src": b.logo_url, "sizes": "512x512", "purpose": "any"}, *icons]
    start = f"/?r={b.login_id}"
    return JSONResponse(
        {
            "id": start,
            "name": b.app_name,
            "short_name": b.app_name[:12],
            "description": f"{b.app_name} billing, KOT and kitchen screens",
            "start_url": start,
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#ffffff",
            "theme_color": b.primary_color,
            "icons": icons,
            "shortcuts": [{"name": "Billing", "url": f"/orders?r={b.login_id}"},
                          {"name": "Kitchen screen", "url": f"/kds?r={b.login_id}"}],
        },
        media_type="application/manifest+json",
    )
