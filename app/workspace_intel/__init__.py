"""Read-only inbox and calendar intelligence composition."""

from .models import AgendaView, FlaggedMeeting, PrioritizedInboxView, PrioritizedMessage
from .service import WorkspaceIntelService

__all__ = (
    "AgendaView",
    "FlaggedMeeting",
    "PrioritizedInboxView",
    "PrioritizedMessage",
    "WorkspaceIntelService",
)
