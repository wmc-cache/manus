declare module "@novnc/novnc/lib/rfb" {
  interface RFBOptions {
    credentials?: { password?: string; username?: string; target?: string };
    wsProtocols?: string[];
  }
  export default class RFB {
    constructor(target: HTMLElement, url: string, options?: RFBOptions);
    viewOnly: boolean;
    scaleViewport: boolean;
    background: string;
    addEventListener(event: string, listener: (e: any) => void): void;
    disconnect(): void;
  }
}
