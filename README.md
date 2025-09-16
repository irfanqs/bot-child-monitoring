# Getting Started

1. Clone this repo <br>
   ```bash
   git clone https://github.com/irfanqs/bot-child-monitoring.git
   cd bot-child-monitoring
   ```
2. Install all dependencies

   ```bash
   pip install -r requirements.txt
   ```

3. Run the code

   ```bash
   python webhook-antares.py
   ```

4. Expose port 5000 using ngrok
   ```bash
   ngrok http 5000
   ```
5. Set webhook on Antares Console
   ```bash
   # dont forget to add /monitor
   https://abcdef-id.ngrok-free.app/monitor
   ```

You're all set!
