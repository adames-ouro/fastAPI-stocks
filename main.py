# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import dash
from dash import dcc, html, Input, Output, State
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np
import threading
from dash.exceptions import PreventUpdate
import sqlite3
from sec_edgar_downloader import Downloader
import os 
from fastapi.staticfiles import StaticFiles

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Initialize the Dash app
dash_app = dash.Dash(__name__)

# Define the layout of the app
dash_app.layout = html.Div([
    html.Div([
    # Dropdown for selecting the period
    html.Label('Time Period:'),
    dcc.Dropdown(
        id='period-dropdown',
        options=[
            {'label': '5 days', 'value': '5d'},
            {'label': '1 month', 'value': '1mo'},
            {'label': '3 months', 'value': '3mo'},
            {'label': '6 months', 'value': '6mo'},
            {'label': '1 year', 'value': '1y'},
            {'label': '2 year', 'value': '2y'},
            {'label': '5 years', 'value': '5y'},
            {'label': '10 years', 'value': '10y'},
        ],
        value='1y'  # default value
    ),

    # Input for entering the stock ticker
    html.Div([
        html.Label('Stock Ticker:'),
        dcc.Input(
            id='stock-input',
            type='text',
            value= 'AAPL'  # default value
        )
    ]),

    # Input for short time window
    html.Div([
        html.Label('Short-Term-EMA:'),
        dcc.Input(
            id='short-time-window-input',
            type='number',
            value=10  # default value
        )
    ]),

    # Input for long time window
    html.Div([
        html.Label('Long-Term-EMA:'),
        dcc.Input(
            id='long-time-window-input',
            type='number',
            value=30  # default value
        )
    ]),

    # Button for refreshing the data
    html.Button('Refresh Visual', id='refresh-button', n_clicks=0),
    ]),
    # Here's the loading component wrapping the graph
    dcc.Loading(
        id="loading",
        type="circle",
        children=[dcc.Graph(id='stock-graph',
                            figure={
                                'layout': {
                                    'xaxis': {'visible': False},
                                    'yaxis': {'visible': False},
                                    'annotations': [{
                                        'text': 'Chart is loading...',
                                        'xref': 'paper',
                                        'yref': 'paper',
                                        'showarrow': False,
                                        'font': {'size': 28}
                                    }]
                                }
                            }
                        ),
            ],
        color="black"
    ),
    ])

# Callback for updating the graph when the dropdown selection changes
@dash_app.callback(
    Output('stock-graph', 'figure'),
    [Input('refresh-button', 'n_clicks')],
    [State('stock-input', 'value'),
     State('period-dropdown', 'value'),
     State('short-time-window-input', 'value'),
     State('long-time-window-input', 'value')])

def update_graph(n_clicks,stock_symbol,selected_period,short_window, long_window):
    # If the button hasn't been clicked, do not update the graph
    if n_clicks is None:
        raise PreventUpdate

    # Fetch the data from yfinance
    stock_symbol = stock_symbol.upper()
    ticker_object = yf.Ticker(stock_symbol)
    stock_df = ticker_object.history(period=selected_period, interval='1d', prepost=True, keepna=False, rounding=2)#.reset_index(drop=False)

    # column names for long and short moving average columns
    short_window_col = f"{str(short_window)}-EMA"
    long_window_col = f"{str(long_window)}-EMA"

    # Create a short simple moving average column
    stock_df[short_window_col] = stock_df['Close'].ewm(span=short_window, adjust=True).mean().round(2)

    # Create a long simple moving average column
    stock_df[long_window_col] = stock_df['Close'].ewm(span=long_window, adjust=True).mean().round(2)

    # create a new column 'Signal' such that if faster moving average is greater than slower moving average 
    # then set Signal as 1 else 0.
    stock_df['Signal'] = 0.0  
    stock_df['Signal'] = np.where(stock_df[short_window_col] > stock_df[long_window_col], 1.0, 0.0) 

    # create a new column 'Position' which is a day-to-day difference of the 'Signal' column. 
    stock_df['Position'] = stock_df['Signal'].diff()

    # remove na
    stock_df = stock_df.fillna(0).drop(columns=['Signal'])

    # Creating the candlestick chart with buy/sell triggers
    fig = go.Figure(data=[go.Candlestick(x=stock_df.index,
                open=stock_df['Open'],
                high=stock_df['High'],
                low=stock_df['Low'],
                close=stock_df['Close'],
                name='Candlestick')])

    fig.add_trace(go.Scatter(x=stock_df.index, y=stock_df['Close'],
                        mode='lines',
                        name='Closing $',
                        line=dict(color='rgba(0, 0, 0, 0.5)')))

    fig.add_trace(go.Scatter(x=stock_df.index, y=stock_df[short_window_col],
                        mode='lines',
                        name=short_window_col,
                        line=dict(color='#CCFFCC')))

    fig.add_trace(go.Scatter(x=stock_df.index, y=stock_df[long_window_col],
                        mode='lines',
                        name=long_window_col,
                        line=dict(color='#FFCCCC')))

    # Add 'buy' signals
    buy_signals = stock_df[stock_df['Position'] == 1]
    fig.add_trace(go.Scatter(x=buy_signals.index, y=buy_signals[short_window_col],
                        mode='markers',
                        marker_symbol='triangle-up',
                        marker_size=15, marker_color='#006400',
                        name='Buy Signal'))

    # Add 'sell' signals
    sell_signals = stock_df[stock_df['Position'] == -1]
    fig.add_trace(go.Scatter(x=sell_signals.index, y=sell_signals[short_window_col],
                        mode='markers',
                        marker_symbol='triangle-down',
                        marker_size=15, marker_color='#8B0000',
                        name='Sell Signal'))

    # Adding shapes and annotations for stock split
    for date, row in stock_df.iterrows():
        if row['Stock Splits'] != 0:
            # Add a vertical line (rectangle) for the stock split dates
            fig.add_shape(go.layout.Shape(type="line",x0=date,y0=0,
                        x1=date, y1=1, yref='paper',line=dict(color="black",width=1,)))

            # Add annotation for the stock split ratio
            fig.add_annotation(x=date,y=0.95,yref='paper',
                              text=f"Stock Split: 1:{int(row['Stock Splits'])}",
                              showarrow=False,font=dict(color="black",size=10),
                              bgcolor="white",opacity=0.7)

    # Add a text card below the X axis title
    if stock_df[stock_df['Position'] != 0.0].iloc[-1]['Position'] == 1.0:
        suggestion = 'Buy'
    elif stock_df[stock_df['Position'] != 0.0].iloc[-1]['Position'] == -1.0:
        suggestion = 'Sell'

    fig.add_annotation(
        x=0.5,  # Positioning in the middle of the X axis
        y=-0.25,  # Positioning below the X axis
        text=f"Suggestion: {suggestion}",  # The text you want to display
        showarrow=False,
        font=dict(
            size=10,
            color="black"
        ),
        align="center",
        bgcolor="white",
        bordercolor="black",
        borderwidth=1,
        borderpad=2,
        xref="paper",  # Reference to the entire paper for positioning
        yref="paper"   # Reference to the entire paper for positioning
    )

    # Customizing the layout
    fig.update_layout(
        title=f"{stock_symbol} Candlestick Plot with Exponential Moving Average Crossover",
        xaxis_rangeslider_visible=False,  # Hide the range slider at the bottom
        xaxis_title='Date',
        yaxis_title='Price in $',
        template='plotly_white')

    # Connect to SQLite database (or create it if it doesn't exist)
    conn = sqlite3.connect('ticker.db')

    # Create a cursor object
    cursor = conn.cursor()

    # Create a table (if it doesn't already exist)
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS my_table (ticker TEXT)''')

    # Insert a row of data
    cursor.execute(f"INSERT INTO my_table (ticker) VALUES (?)", (stock_symbol,))

    # Save (commit) the changes
    conn.commit()

    # Close the connection
    conn.close()
    dash_app.layout['stock-input'].value = stock_symbol
    return fig

# Function to run the Dash app
def run_dash(dash_app):
    dash_app.run_server(port=8050)

def sqlite_ticker():
    conn = sqlite3.connect('ticker.db')
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM my_table")
    results = cursor.fetchall()
    ticker = results[-1][0]
    conn.close()
    return ticker

# Create a thread for the Dash app
dash_thread = threading.Thread(target=run_dash, args=(dash_app,))
dash_thread.start()
app = FastAPI()

# format and stock visual in landing page
@app.get("/", response_class=HTMLResponse)
async def read_items():
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker

    if ticker == None:
        ticker = 'AAPL'
        dash_app.layout['stock-input'].value = ticker
        
    # Assuming the Dash app is running on port 8050
    dash_app_url = "http://localhost:8050"
    return f"""
    <html>
        <head>
            <style>
                .container {{
                    display: flex;
                    justify-content: space-between; /* Aligns items to the center of the main axis */
                }}
                iframe {{
                    border: none;
                    width: 100%;
                    height: 600px;
                }}
                body {{
                    background-color: #f2f2f2;
                    font-family: Arial, sans-serif;
                }}
                h1 {{
                    text-align: center;
                }}
                .center {{
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100px;
                    margin-top: 20px; /* Adjust as needed */
                }}
                .center button {{
                    padding: 10px 20px; /* Adjust as needed */
                    font-size: 16px; /* Adjust as needed */
                }}
            </style>
        </head>
        <body>
            <h1>Stock Information</h1>
            <h6>***This is not financial advice.***</h6>
            <style>
                /* CSS to make forms appear next to each other */
                form {{
                    display: inline-block; 
                    margin-right: 10px; 
                }}
            </style>
        </head>
        <body>
            <iframe src="{dash_app_url}"></iframe>
            <div class="center">
        </body>
        <body>

            <form action="/explanation" method="get">
                <button type="submit">Strategy Explanation</button>
            </form>

            <form action="/statements" method="get">
                <button type="submit">Financial Statements</button>
            </form>

            <form action="/10k" method="get">
                <button type="submit">10-K Report</button>
            </form>

            <form action="/10q" method="get">
                <button type="submit">10-Q Report</button>
            </form>
        </body>
    </html>
    """
    
@app.get("/explanation", response_class=HTMLResponse)
async def get_explanation(request: Request):
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker
    try:
        # Construct the final HTML
        html_content = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>EMA Explanation</title>
                <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
                <style>
                    body {
                        font-family: Arial, sans-serif;
                    }
                    p, li {
                        font-size: 1rem;
                    }
                </style>
            </head>
            <body>

            <h2>Exponential Moving Average (EMA)</h2>

            <p><strong>Formula:</strong></p>
            <body>
                <p>The EMA is computed as:</p>
                <p>\\( EMA_t \\) = ( \\(C_t \\) x \\( alpha \\) ) + (\\( EMA_{t-1} \\) x (1 - \\( alpha \\)) ) </p>
                <p>Where:</p>
                <ul>
                    <li>\\( EMA_t \\): is Exponential Moving Average at time \\( t \\)</li>
                    <li>\\( C_t \\): is Closing price at time \\( t \\)</li>
                    <li>\\( EMA_{t-1} \\): is Exponential Moving Average at time \\( t-1 \\)</li>
                    <li>\\( alpha \\): is Smoothing factor that considers the number of days in moving range period.</li>
                </ul>
            </body>

            <p><strong>Usefulness:</strong></p>

            <p>
                Using both a long and a short time window with Exponential Moving Averages (EMA) is a common strategy in technical analysis for identifying trends, especially in the context of trading. Here's why this approach is popular and beneficial:
            </p>

            <p>
                1. **Trend Identification:** 
                - When the short-term EMA crosses above the long-term EMA, it often indicates the beginning of an upward trend (bullish signal). 
                - Conversely, when the short-term EMA crosses below the long-term EMA, it can be a sign of a downward trend (bearish signal).
            </p>

            <p>
                2. **Sensitivity vs. Stability:**
                - **Short-term EMA** is more sensitive to recent price movements. When prices change, the short-term EMA will reflect this change more quickly than the long-term EMA.
                - **Long-term EMA** is less sensitive to daily price fluctuations and provides a more stable and smoother line that represents long-term trends. 
            </p>

            <p>
                3. **Reduction of False Signals:** 
                - While a short-term EMA might produce many signals (due to its sensitivity), not all of them are indicative of a sustained trend. By requiring confirmation from the long-term EMA (i.e., a crossover), the number of false signals can be reduced.
            </p>

            <p>
                4. **Confirmation and Strength of Trend:**
                - The divergence between the short-term and long-term EMA can give a sense of the strength of a trend. If the two averages are moving apart rapidly, it can indicate a strong trend, while if they start to converge, it might suggest the trend is weakening.
            </p>

            <p>
                5. **Versatility:** 
                - Different pairs of long and short windows can be used depending on the trading strategy, asset being traded, and the trader's time horizon. For instance, a day trader might use a 12-period short-term EMA with a 26-period long-term EMA on a minute chart, while a long-term trader might use the same averages on a daily or weekly chart.
            </p>

            <p>
                6. **Historical Success:** 
                - This approach of using dual EMAs for crossovers has historically been a part of many successful trading strategies, adding to its popularity.
            </p>

            <p>
                In summary, using both a long and a short EMA provides a balance between sensitivity to recent price changes and confirmation of longer-term trends, thereby aiding traders in making more informed decisions. However, like all technical indicators, it's important to use EMA crossovers in conjunction with other tools and methods for the best results.
            </p>

            <div class="center">
            <form action="/" method="get">
                <button type="submit">Visual Page</button>
            </form>

            </body>
            </html>
            '''
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/statements", response_class=HTMLResponse)
async def get_statements(request: Request):
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker
    try:
        # Construct the final HTML
        html_content = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>EMA Explanation</title>
                <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
                <style>
                    body {
                        font-family: Arial, sans-serif;
                    }
                    p, li {
                        font-size: 1rem;
                    }
                </style>
            </head>
            <body>

            <h2>Financial Statements</h2>

            </body>
            </html>
            '''
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  
@app.get("/10k", response_class=HTMLResponse)
async def get_10k(request: Request):
    #forms = ["10-K","10-Q","8-K","DEF 14A","4","S-1","SC 13D","SC 13G","20-F","40-F"]
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker
    dl = Downloader("Ouro Analytics LLC","adames.ouroanalytics.ai",".")
    app.mount("/sec-edgar-filings", StaticFiles(directory="sec-edgar-filings"), name="sec-edgar-filings")
    try:
        dl.get("10-K", ticker, limit=1, include_amends=True,download_details=True)
        path = f'sec-edgar-filings/{ticker}/10-K'
        form_id = os.listdir(path)[0]
        files = os.listdir(path + '/' + form_id)

        for file in files:
            if file.endswith('.html'):
                html_file = file

        file_path = path + '/' + form_id + '/'+ html_file
        with open(file_path, 'r') as file:
            external_html_content = file.read()
       
        html_content = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>SEC 10-K Report</title>
                <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            </head>
            <body>

            <h2>Latest 10-K: {ticker} </h2>
                        
            <style>
                .button-link {{
                    padding: 10px 20px; /* Adjust as needed */
                    font-size: 16px; /* Adjust as needed */
                    background-color: #f2f2f2;
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100px;
                    margin-top: 20px; /* Adjust as needed */
                }}
            </style>
            
            <a href="{file_path}" class="button-link" download>Download 10-K Report</a>
            <form action="/" method="get">
                <button type="submit">Visual Page</button>
            </form>
            {external_html_content}
            </html>
            '''
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/10q", response_class=HTMLResponse)
async def get_10q(request: Request):
    #forms = ["10-K","10-Q","8-K","DEF 14A","4","S-1","SC 13D","SC 13G","20-F","40-F"]
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker
    dl = Downloader("Ouro Analytics LLC","adames.ouroanalytics.ai",".")
    try:
        dl.get("10-Q", ticker, limit=1, include_amends=True,download_details=True)
        path = f'sec-edgar-filings/{ticker}/10-Q'
        form_id = os.listdir(path)[0]
        files = os.listdir(path + '/' + form_id)

        for file in files:
            if file.endswith('.html'):
                html_file = file

        file_path = path + '/' + form_id + '/'+ html_file
        with open(file_path, 'r') as file:
            external_html_content = file.read()
       
        html_content = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>SEC 10-Q Report</title>
                <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            </head>
            <body>

            <h2>Latest 10-Q: {ticker} </h2>
                        
            <style>
                .button-link {{
                    padding: 10px 20px; /* Adjust as needed */
                    font-size: 16px; /* Adjust as needed */
                    background-color: #f2f2f2;
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100px;
                    margin-top: 20px; /* Adjust as needed */
                }}
            </style>
            
            <a href="{file_path}" class="button-link" download>Download 10-Q Report</a>
            <form action="/" method="get">
                <button type="submit">Visual Page</button>
            </form>
            {external_html_content}
            </html>
            '''
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

    #forms = ["10-K","10-Q","8-K","DEF 14A","4","S-1","SC 13D","SC 13G","20-F","40-F"]
    ticker = sqlite_ticker()
    dash_app.layout['stock-input'].value = ticker
    dl = Downloader("Ouro Analytics LLC","adames.ouroanalytics.ai",".")
    try:
        dl.get("SC 13G", ticker, limit=1, include_amends=True,download_details=True)
        path = f'sec-edgar-filings/{ticker}/SC 13G'
        form_id = os.listdir(path)[0]
        files = os.listdir(path + '/' + form_id)

        for file in files:
            if file.endswith('.html'):
                html_file = file

        file_path = path + '/' + form_id + '/'+ html_file
        with open(file_path, 'r') as file:
            external_html_content = file.read()
       
        html_content = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>SEC SC 13G Report</title>
                <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            </head>
            <body>

            <h2>Latest SC 13G: {ticker} </h2>
            
            <style>
                .button-link {{
                    padding: 10px 20px; /* Adjust as needed */
                    font-size: 16px; /* Adjust as needed */
                    background-color: #f2f2f2;
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100px;
                    margin-top: 20px; /* Adjust as needed */
                }}
            </style>

            <a href="{file_path}" class="button-link" download>Download SC 13G Report</a>

            <form action="/" method="get">
                <button type="submit">Visual Page</button>
            </form>

            {external_html_content}

            </html>
            '''
        return HTMLResponse(content=html_content)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))