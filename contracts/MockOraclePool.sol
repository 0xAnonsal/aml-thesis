// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IMintableUSDT is IERC20 {
    function mint(address to, uint256 value) external;
}

/// @notice Minimal Chainlink AggregatorV3 interface — only the fields we use.
interface IAggregatorV3 {
    function latestRoundData()
        external
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        );

    function decimals() external view returns (uint8);
}

/// @title MockOraclePool — oracle-pegged ETH/USDT mock swap contract
/// @notice Same external interface as MockUniswapV2Pool (getReserves,
///         getAmountOut, swapETHForUSDT, swapUSDTForETH, bootstrap,
///         bootstrapped) so tools.py works unmodified. Internally uses
///         a Chainlink price feed to compute swap outputs at *exact* spot
///         rate — no slippage, no constant-product math, no LP depth
///         restriction. Solves the mock-pool-depth artifact documented in
///         TFM §10.4.9.
///
/// @dev    ETH→USDT: mints USDT to sender via permissionless MockUSDT.mint(),
///         keeps the ETH in the contract. Zero pool ETH needed for this path.
///         USDT→ETH: sender sends USDT (contract holds it), contract pays
///         out ETH from its own balance. Requires the contract to hold ≥
///         needed ETH — bootstrap once with a modest reserve (~1 ETH). If
///         a USDT→ETH swap ever exceeds reserve, tx reverts cleanly.
///
///         Research artifact — MUST NOT deploy on a real chain. Assumes
///         MockUSDT.mint() is permissionless (which it is by design).
contract MockOraclePool {
    IMintableUSDT public immutable usdt;
    IAggregatorV3 public immutable oracle;
    address public immutable bootstrapper;

    /// @notice `bootstrapped` mirrors the semantics of MockUniswapV2Pool
    ///         so callers detecting "pool ready" via this flag work unchanged.
    ///         Actual liquidity comes from Chainlink + permissionless USDT mint.
    bool public bootstrapped;

    /// @notice Physical ETH reserve — funds USDT→ETH swaps. Not used for
    ///         ETH→USDT (USDT is minted). Starts at msg.value from bootstrap.
    uint256 public reserveETH;

    /// @dev Oracle price has 8 decimals (standard for Chainlink USD feeds).
    ///      ETH has 18 decimals. USDT has 6 decimals.
    ///      Conversion:  usdt_out = eth_in * oracle_price / 10^20
    ///                   eth_out = usdt_in * 10^20 / oracle_price
    uint256 private constant DECIMAL_ADJUST = 1e20;

    event Bootstrapped(address indexed by, uint256 ethAmount);
    event SwapETHForUSDT(
        address indexed sender, uint256 ethIn, uint256 usdtOut, uint256 oraclePrice
    );
    event SwapUSDTForETH(
        address indexed sender, uint256 usdtIn, uint256 ethOut, uint256 oraclePrice
    );

    constructor(address _usdt, address _oracle) {
        require(_usdt != address(0), "OraclePool: zero usdt");
        require(_oracle != address(0), "OraclePool: zero oracle");
        usdt = IMintableUSDT(_usdt);
        oracle = IAggregatorV3(_oracle);
        bootstrapper = msg.sender;
    }

    /// @notice Seed a small ETH reserve for USDT→ETH swaps. One-shot.
    ///         Unlike constant-product pool, does NOT take USDT — USDT
    ///         supply is elastic via permissionless mint.
    function bootstrap() external payable {
        require(!bootstrapped, "OraclePool: already bootstrapped");
        require(msg.sender == bootstrapper, "OraclePool: only bootstrapper");
        require(msg.value > 0, "OraclePool: zero ETH");
        reserveETH = msg.value;
        bootstrapped = true;
        emit Bootstrapped(msg.sender, msg.value);
    }

    /// @notice Owner top-up of the ETH reserve if it runs low. Anyone can
    ///         call it — extra ETH is welcome.
    function topUpETHReserve() external payable {
        require(msg.value > 0, "OraclePool: zero ETH");
        reserveETH += msg.value;
    }

    /// @notice Current oracle price scaled to Chainlink's decimals (8).
    ///         Reverts if the oracle answer is non-positive.
    function currentOraclePrice() public view returns (uint256) {
        (, int256 answer, , , ) = oracle.latestRoundData();
        require(answer > 0, "OraclePool: bad oracle answer");
        return uint256(answer);
    }

    /// @notice Compatibility shim: constant-product pool exposed
    ///         (reserveETH, reserveUSDT). We return the physical ETH
    ///         reserve and a "virtual" USDT reserve equal to
    ///         reserveETH × oracle_price (i.e., what the constant-product
    ///         invariant would give at exact spot). This lets tools.py
    ///         compute spot ratios and slippage estimates as before,
    ///         though slippage is zero in practice.
    function getReserves() external view returns (uint256, uint256) {
        uint256 price = currentOraclePrice();
        uint256 virtualUsdt = (reserveETH * price) / DECIMAL_ADJUST;
        return (reserveETH, virtualUsdt);
    }

    /// @notice Backward-compat with MockUniswapV2Pool's getAmountOut.
    ///         Ignores reserveIn/reserveOut — always returns oracle-priced
    ///         output. Callers using this to estimate slippage will find
    ///         it is zero regardless of trade size.
    function getAmountOut(
        uint256 amountIn, uint256 reserveIn, uint256 reserveOut
    ) external view returns (uint256) {
        require(amountIn > 0, "OraclePool: zero input");
        uint256 price = currentOraclePrice();
        // Detect direction by which reserve is the "in" side. If
        // reserveIn == reserveETH (physical), the caller is quoting
        // ETH→USDT; otherwise USDT→ETH.
        if (reserveIn == reserveETH) {
            // ETH → USDT
            return (amountIn * price) / DECIMAL_ADJUST;
        } else {
            // USDT → ETH
            return (amountIn * DECIMAL_ADJUST) / price;
        }
    }

    function swapETHForUSDT(uint256 minOut) external payable returns (uint256 usdtOut) {
        require(bootstrapped, "OraclePool: not bootstrapped");
        require(msg.value > 0, "OraclePool: zero ETH input");
        uint256 price = currentOraclePrice();
        usdtOut = (msg.value * price) / DECIMAL_ADJUST;
        require(usdtOut >= minOut, "OraclePool: slippage");
        reserveETH += msg.value;
        usdt.mint(msg.sender, usdtOut);
        emit SwapETHForUSDT(msg.sender, msg.value, usdtOut, price);
    }

    function swapUSDTForETH(uint256 usdtIn, uint256 minOut)
        external
        returns (uint256 ethOut)
    {
        require(bootstrapped, "OraclePool: not bootstrapped");
        require(usdtIn > 0, "OraclePool: zero USDT input");
        uint256 price = currentOraclePrice();
        ethOut = (usdtIn * DECIMAL_ADJUST) / price;
        require(ethOut >= minOut, "OraclePool: slippage");
        require(ethOut <= reserveETH, "OraclePool: insufficient ETH reserve");
        require(
            usdt.transferFrom(msg.sender, address(this), usdtIn),
            "OraclePool: USDT transfer"
        );
        reserveETH -= ethOut;
        (bool sent, ) = msg.sender.call{value: ethOut}("");
        require(sent, "OraclePool: ETH transfer");
        emit SwapUSDTForETH(msg.sender, usdtIn, ethOut, price);
    }

    receive() external payable {
        reserveETH += msg.value;
    }
}
