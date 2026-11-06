"""EcoFlow (Smart Home Panel 3 + Delta Pro Ultra X) app entry point."""

from homey.app import App


class EcoFlowApp(App):
    async def on_init(self) -> None:
        self.log("EcoFlow app has been initialized")


homey_export = EcoFlowApp
