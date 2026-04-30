// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IWETH {
    function deposit() external payable;
}

/// @notice Shared intent struct used across the system.
struct UserIntent {
    address user;
    address tokenIn;
    address tokenOut;
    uint256 amountIn;
    uint256 minAmountOut;
    uint256 deadline;
    bytes32 salt;            // Nonce for unique intent hash
    bool allowPartialFill;   // Whether partial execution is allowed
}

/// @title IntentVault
/// @notice Escrow vault for user deposits. Only SettlementRouter can move funds.
contract IntentVault is Ownable {
    using SafeERC20 for IERC20;

    address public settlementRouter;
    address public weth;

    // user => token => balance
    mapping(address => mapping(address => uint256)) public balances;

    event Deposited(address indexed user, address indexed token, uint256 amount);
    event Withdrawn(address indexed user, address indexed token, uint256 amount);
    event RouterSet(address indexed router);

    constructor(address _weth) Ownable(msg.sender) {
        weth = _weth;
    }

    function setSettlementRouter(address _router) external onlyOwner {
        require(_router != address(0), "Invalid router address");
        settlementRouter = _router;
        emit RouterSet(_router);
    }

    function setWETH(address _weth) external onlyOwner {
        require(_weth != address(0), "Invalid WETH address");
        weth = _weth;
    }

    /// @notice Deposit ERC20 tokens into the vault.
    function deposit(address token, uint256 amount) external {
        require(amount > 0, "Amount must be > 0");
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        balances[msg.sender][token] += amount;
        emit Deposited(msg.sender, token, amount);
    }

    /// @notice Deposit ETH, auto-wraps to WETH.
    function depositETH() external payable {
        require(msg.value > 0, "Amount must be > 0");
        require(weth != address(0), "WETH address not set");
        IWETH(weth).deposit{value: msg.value}();
        balances[msg.sender][weth] += msg.value;
        emit Deposited(msg.sender, weth, msg.value);
    }

    /// @notice Withdraw unused tokens from the vault.
    function withdraw(address token, uint256 amount) external {
        require(amount > 0, "Amount must be > 0");
        require(balances[msg.sender][token] >= amount, "Insufficient balance");
        balances[msg.sender][token] -= amount;
        IERC20(token).safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, token, amount);
    }

    /// @notice Router-only: transfer user funds out for settlement.
    function routerTransfer(address user, address token, address to, uint256 amount) external {
        require(msg.sender == settlementRouter, "Unauthorized: only router");
        require(balances[user][token] >= amount, "Insufficient vault balance");
        balances[user][token] -= amount;
        IERC20(token).safeTransfer(to, amount);
    }

    /// @notice Router-only: refund unconsumed tokens back to user's vault balance.
    function refund(address user, address token, uint256 amount) external {
        require(msg.sender == settlementRouter, "Unauthorized: only router");
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        balances[user][token] += amount;
        emit Deposited(user, token, amount);
    }
}
