"""
ApexSpreadator — Simple Dashboard Server
Serves the backtest report locally and launches the web browser.
"""
import os
import sys
import http.server
import socketserver
import webbrowser
import threading

PORT = 8088
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    os.chdir(PROJECT_DIR)

    # Validate that at least one of the dashboard's required data sets exists
    std_required = ["data/backtest_report.json", "data/backtest_trades.json", "data/backtest_equity_curve.csv"]
    std_missing = [f for f in std_required if not os.path.exists(f)]
    
    mc_required = ["data/symbol_analysis_results.json"]
    mc_missing = [f for f in mc_required if not os.path.exists(f)]
    
    if std_missing and mc_missing:
        print("⚠ Missing data files for both standard backtest and Monte Carlo:")
        print("  Standard files missing:", std_missing)
        print("  Monte Carlo files missing:", mc_missing)
        print("\nPlease run a pipeline to generate report data:")
        print("  Standard: python run_pipeline.py --years 3 --capital 25000 --symbols SPY QQQ")
        print("  Monte Carlo: python tools/sector_backtest.py")
        sys.exit(1)

    handler = http.server.SimpleHTTPRequestHandler
    handler.extensions_map.update({
        '.json': 'application/json',
        '.csv': 'text/csv',
    })

    port = PORT
    max_port = PORT + 10
    httpd = None

    while port <= max_port:
        try:
            httpd = socketserver.TCPServer(("", port), handler)
            break
        except OSError as e:
            if "Only one usage of each socket address" in str(e) or "10048" in str(e):
                port += 1
            else:
                raise

    if not httpd:
        print(f"❌ Could not find an open port between {PORT} and {max_port}.")
        sys.exit(1)

    with httpd:
        url_backtest = f"http://localhost:{port}/dashboard/backtest_report.html"
        url_mc = f"http://localhost:{port}/dashboard/monte_carlo.html"
        print(f"╔══════════════════════════════════════════════╗")
        print(f"║  ApexSpreadator Dashboard Server             ║")
        print(f"╠══════════════════════════════════════════════╣")
        print(f"║  Backtest Report: {url_backtest:<27} ║")
        print(f"║  Monte Carlo DB : {url_mc:<27} ║")
        print(f"║  Press Ctrl+C to stop                       ║")
        print(f"╚══════════════════════════════════════════════╝")

        # Start browser after short delay
        threading.Timer(0.5, lambda: webbrowser.open(url_mc)).start()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            httpd.shutdown()


if __name__ == "__main__":
    main()
