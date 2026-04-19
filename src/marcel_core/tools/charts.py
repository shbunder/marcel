"""Chart and image generation tool for Marcel.

Uses matplotlib to render charts server-side. The generated PNG is stored as
an artifact and sent directly as a Telegram photo (native inline display).

For Telegram channels, the image is sent immediately via ``sendPhoto``.
For other channels, the tool returns a text description and the artifact ID.
"""

from __future__ import annotations

import io
import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def generate_chart(
    ctx: RunContext[MarcelDeps],
    code: str,
    title: str = 'Chart',
) -> str:
    """Generate a chart or image using matplotlib and send it to the user.

    Write standard matplotlib code that creates a figure. The code runs in a
    sandboxed namespace with ``matplotlib.pyplot`` available as ``plt`` and
    ``numpy`` as ``np``. Do NOT call ``plt.show()`` — the figure is captured
    automatically.

    The generated image is:
    - Stored as an artifact for later viewing
    - Sent directly as a photo in Telegram (no "View in app" button needed)
    - Returned as an artifact reference for other channels

    Args:
        ctx: Agent context with user and conversation info.
        code: Python code that creates a matplotlib figure. Use ``plt`` and
            ``np`` directly. The active figure is captured after execution.
            Example: ``plt.bar(['A', 'B', 'C'], [10, 20, 15]); plt.title('Example')``
        title: Short description of the chart for the artifact title.

    Returns:
        Confirmation message with artifact ID, or error details.
    """
    import matplotlib

    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import numpy as np

    # Set dark theme to match Marcel/Telegram aesthetics
    plt.style.use('dark_background')

    try:
        # Execute the user's chart code in a controlled namespace
        namespace: dict = {'plt': plt, 'np': np}
        exec(code, namespace)  # noqa: S102

        # Capture the active figure
        fig = plt.gcf()
        if not fig.get_axes():
            plt.close('all')
            return (
                'Error: No chart was created. Make sure your code creates a matplotlib figure with at least one axes.'
            )

        # Render to PNG bytes
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
        buf.seek(0)
        png_bytes = buf.getvalue()
        plt.close('all')

    except Exception as exc:
        plt.close('all')
        return f'Error generating chart: {exc}'

    # Store as artifact
    try:
        from marcel_core.storage.artifacts import create_artifact, files_dir

        # Save the PNG file
        artifact_id = create_artifact(
            user_slug=ctx.deps.user_slug,
            conversation_id=ctx.deps.conversation_id or '',
            content_type='image',
            content='',  # Will be updated with filename
            title=title,
        )

        # Write the PNG to the artifact files directory
        png_path = files_dir() / f'{artifact_id}.png'
        png_path.write_bytes(png_bytes)

        # Update the artifact content to point to the file
        from marcel_core.storage.artifacts import load_artifact, save_artifact

        artifact = load_artifact(artifact_id)
        if artifact:
            artifact.content = f'{artifact_id}.png'
            save_artifact(artifact)

    except Exception as exc:
        log.exception('Failed to store chart artifact')
        return f'Chart generated but failed to store: {exc}'

    # Send directly as Telegram photo if on Telegram channel
    if ctx.deps.channel == 'telegram':
        try:
            from marcel_core.plugin import get_channel

            channel = get_channel('telegram')
            if channel is not None and await channel.send_photo(ctx.deps.user_slug, png_bytes, caption=title):
                return f'Chart sent to Telegram. Artifact ID: {artifact_id}'
        except Exception as exc:
            log.warning('Failed to send chart photo to Telegram: %s', exc)
            return f'Chart stored as artifact {artifact_id} but failed to send photo: {exc}'

    return f'Chart generated and stored. Artifact ID: {artifact_id}'
