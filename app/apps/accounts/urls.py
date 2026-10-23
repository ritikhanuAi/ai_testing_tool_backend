from fastapi import APIRouter, status
from app.common.serializers import ResponseEnvelope
from app.apps.accounts.serializers import UserResponse, TokenResponse
from app.apps.accounts.views import register_view, login_view, me_view

router = APIRouter(prefix="/accounts", tags=["Accounts"])

router.post("/register", response_model=ResponseEnvelope[UserResponse], status_code=status.HTTP_201_CREATED, summary="Register New User")(register_view)
router.post("/login", response_model=ResponseEnvelope[TokenResponse], summary="User Login")(login_view)
router.get("/me", response_model=ResponseEnvelope[UserResponse], summary="Current User Details")(me_view)
