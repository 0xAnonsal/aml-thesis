// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @title MockUniswapV2Pool — research artifact
/// @notice Minimal constant-product AMM for an ETH/USDT pair. 0.3% fee. No
///         LP tokens — single bootstrapper seeds liquidity once, then anyone
///         can swap. Slippage and fee dynamics match Uniswap V2 closely
///         enough for AML simulation: the launderer pays real cost when
///         swapping USDT <-> ETH on the way to/from the mixer.
/// @dev    Hand-written to avoid OpenZeppelin/Uniswap submodules. Reserves
///         are tracked in storage; we never sync from balances. Local fork
///         use only. NEVER deploy on a real chain.
contract MockUniswapV2Pool {
    IERC20 public immutable usdt;
    address public immutable bootstrapper;

    uint256 public reserveETH;
    uint256 public reserveUSDT;
    bool public bootstrapped;

    uint256 private constant FEE_NUMERATOR = 997;     // 0.3% fee = 1 - 997/1000
    uint256 private constant FEE_DENOMINATOR = 1000;

    event Bootstrapped(address indexed by, uint256 ethAmount, uint256 usdtAmount);
    event SwapETHForUSDT(address indexed sender, uint256 ethIn, uint256 usdtOut);
    event SwapUSDTForETH(address indexed sender, uint256 usdtIn, uint256 ethOut);

    constructor(address _usdt) {
        require(_usdt != address(0), "Pool: zero usdt");
        usdt = IERC20(_usdt);
        bootstrapper = msg.sender;
    }

    /// @notice Seed initial liquidity. Caller must approve `usdtAmount` first
    ///         on the USDT contract; sends ETH as msg.value. One-shot.
    function bootstrap(uint256 usdtAmount) external payable {
        require(!bootstrapped, "Pool: already bootstrapped");
        require(msg.sender == bootstrapper, "Pool: only bootstrapper");
        require(msg.value > 0 && usdtAmount > 0, "Pool: zero liquidity");
        require(usdt.transferFrom(msg.sender, address(this), usdtAmount), "Pool: USDT transfer");
        reserveETH = msg.value;
        reserveUSDT = usdtAmount;
        bootstrapped = true;
        emit Bootstrapped(msg.sender, msg.value, usdtAmount);
    }

    /// @notice Constant-product output quote, fee-inclusive. Pure function.
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut)
        public
        pure
        returns (uint256)
    {
        require(amountIn > 0, "Pool: zero input");
        require(reserveIn > 0 && reserveOut > 0, "Pool: empty reserves");
        uint256 amountInWithFee = amountIn * FEE_NUMERATOR;
        uint256 numerator = amountInWithFee * reserveOut;
        uint256 denominator = reserveIn * FEE_DENOMINATOR + amountInWithFee;
        return numerator / denominator;
    }

    function swapETHForUSDT(uint256 minOut) external payable returns (uint256 usdtOut) {
        require(bootstrapped, "Pool: not bootstrapped");
        require(msg.value > 0, "Pool: zero ETH input");
        usdtOut = getAmountOut(msg.value, reserveETH, reserveUSDT);
        require(usdtOut >= minOut, "Pool: slippage");
        reserveETH += msg.value;
        reserveUSDT -= usdtOut;
        require(usdt.transfer(msg.sender, usdtOut), "Pool: USDT transfer");
        emit SwapETHForUSDT(msg.sender, msg.value, usdtOut);
    }

    function swapUSDTForETH(uint256 usdtIn, uint256 minOut) external returns (uint256 ethOut) {
        require(bootstrapped, "Pool: not bootstrapped");
        require(usdtIn > 0, "Pool: zero USDT input");
        ethOut = getAmountOut(usdtIn, reserveUSDT, reserveETH);
        require(ethOut >= minOut, "Pool: slippage");
        require(usdt.transferFrom(msg.sender, address(this), usdtIn), "Pool: USDT transfer");
        reserveUSDT += usdtIn;
        reserveETH -= ethOut;
        (bool sent, ) = msg.sender.call{value: ethOut}("");
        require(sent, "Pool: ETH transfer");
        emit SwapUSDTForETH(msg.sender, usdtIn, ethOut);
    }

    function getReserves() external view returns (uint256, uint256) {
        return (reserveETH, reserveUSDT);
    }

    receive() external payable {}
}
